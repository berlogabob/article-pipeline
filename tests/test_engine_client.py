import subprocess

import httpx
import pytest

from article_pipeline import engine
from article_pipeline.engine import CliEngine, HttpEngine, read_omlx_api_key


# ---------------------------------------------------------------------------
# HttpEngine
# ---------------------------------------------------------------------------


def test_http_engine_list_models_parses_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "llama3"}, {"id": "mistral"}]})

    transport = httpx.MockTransport(handler)
    eng = HttpEngine("http://localhost:11434/v1", "ollama", 5, transport=transport)

    assert eng.list_models() == ["llama3", "mistral"]


def test_http_engine_chat_json_returns_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read()
        import json

        payload = json.loads(body)
        assert payload["model"] == "llama3"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    transport = httpx.MockTransport(handler)
    eng = HttpEngine("http://localhost:11434/v1", "ollama", 5, transport=transport)

    result = eng.chat_json("say hi", "llama3", 0.05)
    assert result == '{"ok": true}'


def test_http_engine_chat_json_retries_without_response_format_on_400():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.read())
        calls.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    transport = httpx.MockTransport(handler)
    eng = HttpEngine("http://localhost:11434/v1", "ollama", 5, transport=transport)

    result = eng.chat_json("say hi", "llama3", 0.05)

    assert result == '{"ok": true}'
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_http_engine_chat_json_raises_on_persistent_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(handler)
    eng = HttpEngine("http://localhost:11434/v1", "ollama", 5, transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        eng.chat_json("say hi", "llama3", 0.05)


# ---------------------------------------------------------------------------
# CliEngine
# ---------------------------------------------------------------------------


def test_cli_engine_claude_command_assembled(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    eng = CliEngine("claude", 30)
    result = eng.chat_json("say hi", "sonnet", 0.05)

    assert result == '{"ok": true}'
    assert captured["cmd"] == [
        "claude",
        "-p",
        "say hi",
        "--model",
        "sonnet",
        "--output-format",
        "text",
    ]
    assert captured["timeout"] == 30


def test_cli_engine_claude_omits_model_flag_when_empty(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    eng = CliEngine("claude", 30)
    eng.chat_json("say hi", "", 0.05)

    assert "--model" not in captured["cmd"]
    assert captured["cmd"] == ["claude", "-p", "say hi", "--output-format", "text"]


def test_cli_engine_codex_command_assembled(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        # Simulate codex writing the last message to the --output-last-message file.
        out_path = cmd[cmd.index("--output-last-message") + 1]
        with open(out_path, "w") as f:
            f.write('{"ok": true}')
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    eng = CliEngine("codex", 30)
    result = eng.chat_json("say hi", "o4-mini", 0.05)

    assert result == '{"ok": true}'
    cmd = captured["cmd"]
    assert cmd[:4] == ["codex", "exec", "-s", "read-only"]
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "o4-mini"
    assert "--output-last-message" in cmd
    assert cmd[-1] == "say hi"


def test_cli_engine_timeout_raises(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    eng = CliEngine("claude", 5)
    with pytest.raises(TimeoutError):
        eng.chat_json("say hi", "sonnet", 0.05)


def test_cli_engine_nonzero_exit_raises_runtime_error(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom went wrong")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    eng = CliEngine("claude", 5)
    with pytest.raises(RuntimeError, match="boom went wrong"):
        eng.chat_json("say hi", "sonnet", 0.05)


def test_cli_engine_list_models():
    assert CliEngine("claude", 5).list_models() == ["opus", "sonnet", "haiku"]
    assert CliEngine("codex", 5).list_models() == []


# ---------------------------------------------------------------------------
# read_omlx_api_key
# ---------------------------------------------------------------------------


def test_read_omlx_api_key_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "OMLX_SETTINGS_PATH", tmp_path / "settings.json")
    with pytest.raises(RuntimeError, match="oMLX"):
        read_omlx_api_key()


def test_read_omlx_api_key_missing_key(monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"auth": {}}')
    monkeypatch.setattr(engine, "OMLX_SETTINGS_PATH", settings_path)
    with pytest.raises(RuntimeError):
        read_omlx_api_key()


def test_read_omlx_api_key_success(monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"auth": {"api_key": "sk-test-123"}}')
    monkeypatch.setattr(engine, "OMLX_SETTINGS_PATH", settings_path)
    assert read_omlx_api_key() == "sk-test-123"
