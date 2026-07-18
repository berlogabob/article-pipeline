"""LLM engine clients: HTTP (ollama/oMLX, OpenAI-compatible) and CLI (claude/codex).

Both flavors expose the same interface:
  - list_models() -> list[str]
  - chat_json(prompt, model, temperature) -> str  (raw text; caller does JSON cleanup)
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

from article_pipeline.config import Config

CLAUDE_MODELS = ["opus", "sonnet", "haiku"]
CODEX_MODELS: list[str] = []

OMLX_SETTINGS_PATH = Path.home() / ".omlx" / "settings.json"


class HttpEngine:
    """OpenAI-compatible HTTP engine (ollama, oMLX)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._transport = transport  # injected in tests via httpx.MockTransport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_seconds, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def list_models(self) -> list[str]:
        with self._client() as client:
            resp = client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data["data"]]

    def chat_json(
        self, prompt: str, model: str, temperature: float, max_tokens: int = 2048
    ) -> str:
        # max_tokens caps runaway generation (oMLX's server default is 65k)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        with self._client() as client:
            resp = client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
            )
            if resp.status_code == 400:
                # Server may not support response_format; retry without it.
                payload.pop("response_format", None)
                resp = client.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
                )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


class CliEngine:
    """CLI-driven engine (claude, codex), one-shot subprocess per call."""

    def __init__(self, provider: str, timeout_seconds: float):
        if provider not in ("claude", "codex"):
            raise ValueError(f"unsupported CLI provider: {provider}")
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    def list_models(self) -> list[str]:
        if self.provider == "claude":
            return list(CLAUDE_MODELS)
        return list(CODEX_MODELS)

    def chat_json(self, prompt: str, model: str, temperature: float) -> str:
        # "/no_think" is a hint for local Qwen-style models; CLI agents don't
        # need it, and claude CLI would parse a leading "/" as a slash command.
        stripped = prompt.lstrip()
        if stripped.startswith("/no_think"):
            prompt = stripped[len("/no_think"):].lstrip("\n")
        if self.provider == "claude":
            return self._chat_claude(prompt, model)
        return self._chat_codex(prompt, model)

    def _run(self, cmd: list[str]) -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"{self.provider} CLI timed out after {self.timeout_seconds}s"
            ) from exc

        if result.returncode != 0:
            stderr_snippet = (result.stderr or "").strip()[:500]
            raise RuntimeError(
                f"{self.provider} CLI exited with code {result.returncode}: {stderr_snippet}"
            )
        return result.stdout

    def _chat_claude(self, prompt: str, model: str) -> str:
        cmd = ["claude", "-p", prompt]
        if model:
            cmd += ["--model", model]
        cmd += ["--output-format", "text"]
        return self._run(cmd)

    def _chat_codex(self, prompt: str, model: str) -> str:
        with tempfile.NamedTemporaryFile(
            mode="r", suffix=".txt", delete=False
        ) as tmp:
            tmp_path = tmp.name
        try:
            cmd = ["codex", "exec", "-s", "read-only"]
            if model:
                cmd += ["-m", model]
            cmd += ["--output-last-message", tmp_path, prompt]
            self._run(cmd)
            return Path(tmp_path).read_text()
        finally:
            Path(tmp_path).unlink(missing_ok=True)


def read_omlx_api_key() -> str:
    """Read the oMLX API key from $OMLX_API_KEY or ~/.omlx/settings.json (auth.api_key)."""
    env_key = os.getenv("OMLX_API_KEY", "").strip()
    if env_key:
        return env_key
    if not OMLX_SETTINGS_PATH.exists():
        raise RuntimeError(
            f"oMLX settings not found at {OMLX_SETTINGS_PATH}. "
            "Install/configure oMLX first (it must have run once to write this file)."
        )
    try:
        settings = json.loads(OMLX_SETTINGS_PATH.read_text())
        api_key = settings["auth"]["api_key"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Could not read auth.api_key from {OMLX_SETTINGS_PATH}. "
            "Reconfigure oMLX so it writes a valid api_key."
        ) from exc
    if not api_key:
        raise RuntimeError(
            f"auth.api_key is empty in {OMLX_SETTINGS_PATH}. Reconfigure oMLX."
        )
    return api_key


def make_engine(cfg: Config):
    """Build the engine client configured by cfg.engine."""
    provider = cfg.engine.provider
    timeout = cfg.llm.timeout_seconds

    if provider == "ollama":
        return HttpEngine("http://localhost:11434/v1", "ollama", timeout)
    if provider == "omlx":
        return HttpEngine("http://127.0.0.1:8000/v1", read_omlx_api_key(), timeout)
    if provider in ("claude", "codex"):
        return CliEngine(provider, timeout)
    raise ValueError(f"unknown or unset engine provider: {provider!r}")
