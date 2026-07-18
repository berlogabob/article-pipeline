import shutil
from pathlib import Path

import pytest

from article_pipeline import wizard
from article_pipeline.config import Config, EngineConfig, load_config, save_config

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "config.example.yaml"


@pytest.fixture
def project_root(tmp_path):
    """A fake project root with config.example.yaml but no config.yaml."""
    shutil.copy(EXAMPLE_CONFIG, tmp_path / "config.example.yaml")
    return tmp_path


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_none_when_missing(project_root):
    assert load_config(project_root) is None


def test_load_config_none_when_provider_null(project_root):
    # config.yaml exists but doesn't set engine.provider -> still needs wizard.
    (project_root / "config.yaml").write_text("output_format: markdown\n")
    assert load_config(project_root) is None


def test_save_load_roundtrip_preserves_wizard_answers(project_root):
    cfg = Config()
    cfg.engine = EngineConfig(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        primary_model="llama3",
        fallback_model="mistral",
    )
    cfg.output_format = "typst"

    save_config(cfg, project_root)
    assert (project_root / "config.yaml").exists()

    loaded = load_config(project_root)
    assert loaded is not None
    assert loaded.engine.provider == "ollama"
    assert loaded.engine.base_url == "http://localhost:11434/v1"
    assert loaded.engine.primary_model == "llama3"
    assert loaded.engine.fallback_model == "mistral"
    assert loaded.output_format == "typst"
    # Defaults from config.example.yaml still present.
    assert loaded.content.min_length == 100
    assert loaded.folders.inbox == "01_inbox"


def test_load_config_overlays_defaults(project_root):
    # Partial config.yaml should still inherit unset fields from config.example.yaml.
    (project_root / "config.yaml").write_text(
        "engine:\n  provider: claude\n  primary_model: sonnet\n"
    )
    cfg = load_config(project_root)
    assert cfg is not None
    assert cfg.engine.provider == "claude"
    assert cfg.engine.primary_model == "sonnet"
    assert cfg.llm.timeout_seconds == 120  # inherited default
    assert cfg.typst.related_articles.max_links == 5  # nested default


# ---------------------------------------------------------------------------
# run_wizard
# ---------------------------------------------------------------------------


class FakeHttpEngine:
    def list_models(self):
        return ["llama3", "mistral"]


class FakeCliEngine:
    def list_models(self):
        return []


def test_wizard_http_engine_flow(project_root, monkeypatch):
    monkeypatch.setattr(wizard, "make_engine", lambda cfg: FakeHttpEngine())

    inputs = iter(
        [
            "1",  # provider -> ollama
            "1",  # primary model -> llama3
            "2",  # fallback model -> mistral
            "1",  # output format -> markdown
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_args: next(inputs))

    cfg = wizard.run_wizard(project_root)

    assert cfg.engine.provider == "ollama"
    assert cfg.engine.primary_model == "llama3"
    assert cfg.engine.fallback_model == "mistral"
    assert cfg.output_format == "markdown"

    reloaded = load_config(project_root)
    assert reloaded is not None
    assert reloaded.engine.provider == "ollama"
    assert reloaded.engine.primary_model == "llama3"
    assert reloaded.engine.fallback_model == "mistral"
    assert reloaded.output_format == "markdown"


def test_wizard_cli_engine_flow(project_root, monkeypatch):
    monkeypatch.setattr(wizard, "make_engine", lambda cfg: FakeCliEngine())
    monkeypatch.setattr(wizard.shutil, "which", lambda _binary: "/usr/local/bin/claude")

    inputs = iter(
        [
            "3",  # provider -> claude
            "sonnet",  # free-text primary model
            "",  # fallback -> none
            "2",  # output format -> typst
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_args: next(inputs))

    cfg = wizard.run_wizard(project_root)

    assert cfg.engine.provider == "claude"
    assert cfg.engine.primary_model == "sonnet"
    assert cfg.engine.fallback_model is None
    assert cfg.output_format == "typst"


def test_wizard_cli_engine_missing_binary_exits(project_root, monkeypatch):
    monkeypatch.setattr(wizard.shutil, "which", lambda _binary: None)

    inputs = iter(["3"])  # provider -> claude
    monkeypatch.setattr("builtins.input", lambda *_args: next(inputs))

    with pytest.raises(SystemExit):
        wizard.run_wizard(project_root)


def test_wizard_http_engine_connect_error_exits(project_root, monkeypatch):
    import httpx

    class DeadEngine:
        def list_models(self):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(wizard, "make_engine", lambda cfg: DeadEngine())

    inputs = iter(["1"])  # provider -> ollama
    monkeypatch.setattr("builtins.input", lambda *_args: next(inputs))

    with pytest.raises(SystemExit):
        wizard.run_wizard(project_root)
