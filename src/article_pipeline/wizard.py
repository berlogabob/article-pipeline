"""Interactive first-run config wizard. stdlib input() only, no rich deps."""

import shutil
import sys
from pathlib import Path

import httpx

from article_pipeline.config import Config, EngineConfig, save_config
from article_pipeline.engine import make_engine, read_omlx_api_key

PROVIDERS = ["ollama", "omlx", "claude", "codex"]
OUTPUT_FORMATS = ["markdown", "typst"]


def _choose(prompt: str, options: list[str]) -> str:
    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(f"Enter 1-{len(options)}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("Invalid choice, try again.")


def _choose_model(models: list[str], label: str, allow_none: bool = False) -> str | None:
    if not models:
        return None
    print(f"Available {label} models:")
    for i, m in enumerate(models, start=1):
        print(f"  {i}. {m}")
    if allow_none:
        print("  0. (none)")
    while True:
        raw = input("Enter number: ").strip()
        if allow_none and (raw == "" or raw == "0"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(models):
            return models[int(raw) - 1]
        print("Invalid choice, try again.")


def run_wizard(root: Path) -> Config:
    provider = _choose("Choose LLM engine:", PROVIDERS)

    if provider == "omlx":
        read_omlx_api_key()  # fail fast with a clear error if unconfigured
    elif provider in ("claude", "codex"):
        if shutil.which(provider) is None:
            print(
                f"Error: '{provider}' CLI not found on PATH. "
                f"Install it and re-run the wizard.",
                file=sys.stderr,
            )
            sys.exit(1)

    base_url = None
    if provider == "ollama":
        base_url = "http://localhost:11434/v1"
    elif provider == "omlx":
        base_url = "http://127.0.0.1:8000/v1"

    tmp_engine_cfg = EngineConfig(provider=provider, base_url=base_url)
    tmp_cfg = Config(engine=tmp_engine_cfg)
    engine = make_engine(tmp_cfg)

    try:
        models = engine.list_models()
    except httpx.ConnectError:
        print(
            f"Error: could not connect to the {provider} server at {base_url}. "
            "Start the server and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    if provider in ("ollama", "omlx"):
        if not models:
            print(f"Error: no models available from {provider} server.", file=sys.stderr)
            sys.exit(1)
        print("Available models:")
        for i, m in enumerate(models, start=1):
            print(f"  {i}. {m}")
        while True:
            raw = input("Choose primary model number: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(models):
                primary_model = models[int(raw) - 1]
                break
            print("Invalid choice, try again.")

        print("Choose fallback model number (0 or empty = none):")
        while True:
            raw = input("Fallback: ").strip()
            if raw == "" or raw == "0":
                fallback_model = None
                break
            if raw.isdigit() and 1 <= int(raw) <= len(models):
                fallback_model = models[int(raw) - 1]
                break
            print("Invalid choice, try again.")
    else:
        # CLI engines: curated list (may be empty) + free text.
        primary_model = _choose_model(models, provider, allow_none=False) if models else None
        if primary_model is None:
            raw = input(
                f"Enter {provider} model name (empty = CLI default): "
            ).strip()
            primary_model = raw or None
        raw = input("Enter fallback model name (empty = none): ").strip()
        fallback_model = raw or None

    output_format = _choose("Choose output format:", OUTPUT_FORMATS)

    cfg = Config()
    cfg.engine = EngineConfig(
        provider=provider,
        base_url=base_url,
        primary_model=primary_model,
        fallback_model=fallback_model,
    )
    cfg.output_format = output_format

    save_config(cfg, root)

    print("\nConfig saved to config.yaml:")
    print(f"  engine: {provider}")
    print(f"  base_url: {base_url}")
    print(f"  primary_model: {primary_model}")
    print(f"  fallback_model: {fallback_model}")
    print(f"  output_format: {output_format}")

    return cfg
