import json

import httpx
import pytest

from article_pipeline import engine, worker
from article_pipeline.config import Config


@pytest.fixture
def worker_run(tmp_path, monkeypatch):
    cfg = Config()
    monkeypatch.setattr(worker, "load_config", lambda root: cfg)
    seen = []

    def drain(config, connection_id="studio"):
        seen.append((config.engine.model_dump(), engine.make_engine(config, connection_id)))
        return 0, 0

    monkeypatch.setattr(worker, "drain_jobs", drain)

    def run(settings=None):
        if settings is not None:
            path = tmp_path / "_system/settings/ai.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings), encoding="utf-8")
        assert worker.main(["--once", "--vault", str(tmp_path)]) == 0
        return seen[-1]

    return run


def settings(connection="studio", model="primary", fallback="fallback"):
    return {"schema": 1, "connections": [{"id": connection, "url": "http://host:8888/v1"}],
            "tasks": {"articles": {"connection": connection, "model": model,
                                   "fallback_model": fallback}}}


def assert_defaults(config, client):
    assert config["base_url"] == engine.STUDIO_BASE_URL
    assert config["primary_model"] == engine.STUDIO_PRIMARY_MODEL
    assert config["fallback_model"] == engine.STUDIO_FALLBACK_MODEL
    assert client.connection_id == "studio"


def test_no_file(worker_run):
    assert_defaults(*worker_run())


def test_valid_override(worker_run, monkeypatch):
    config, client = worker_run(settings("remote-studio"))
    assert config["base_url"] == "http://host:8888/v1"
    assert config["primary_model"] == "primary"
    assert config["fallback_model"] == "fallback"
    assert client.connection_id == "remote-studio"
    monkeypatch.delenv("TYLOG_LLM_KEY", raising=False)
    monkeypatch.setenv("TYLOG_KEY_REMOTE_STUDIO", "connection-key")

    def handler(request):
        assert request.url == "http://host:8888/v1/models"
        assert request.headers["Authorization"] == "Bearer connection-key"
        return httpx.Response(200, json={"data": [{"id": "primary"}]})

    client._transport = httpx.MockTransport(handler)
    assert client.list_models() == ["primary"]


def test_unknown_connection(worker_run):
    data = settings()
    data["tasks"]["articles"]["connection"] = "unknown"
    assert_defaults(*worker_run(data))


@pytest.mark.parametrize("model,fallback", [(None, None), ("", ""), (" ", " ")])
def test_empty_models_keep_defaults(worker_run, model, fallback):
    config, _ = worker_run(settings(model=model, fallback=fallback))
    assert config["base_url"] == "http://host:8888/v1"
    assert config["primary_model"] == engine.STUDIO_PRIMARY_MODEL
    assert config["fallback_model"] == engine.STUDIO_FALLBACK_MODEL


@pytest.mark.parametrize("data", [None, [], {}, {"schema": 2},
                                  {"schema": 1, "connections": None},
                                  settings(model=123)])
def test_invalid_settings_keep_defaults(worker_run, tmp_path, data):
    path = tmp_path / "_system/settings/ai.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert_defaults(*worker_run())


def test_invalid_json_keeps_defaults(worker_run, tmp_path):
    path = tmp_path / "_system/settings/ai.json"
    path.parent.mkdir(parents=True)
    path.write_text("{invalid", encoding="utf-8")
    assert_defaults(*worker_run())


@pytest.mark.parametrize("connection_id", ["studio", "remote.studio-2"])
def test_key_lookup_order(tmp_path, monkeypatch, connection_id, caplog):
    monkeypatch.setenv("HOME", str(tmp_path))
    env_name = "TYLOG_KEY_" + connection_id.upper().replace(".", "_").replace("-", "_")
    monkeypatch.setenv("TYLOG_LLM_KEY", "global-secret")
    monkeypatch.setenv(env_name, "connection-secret")
    keys = tmp_path / ".tylog/keys.json"
    keys.parent.mkdir()
    keys.write_text(json.dumps({connection_id: "local-secret"}))
    pi = tmp_path / ".pi/agent/models.json"
    pi.parent.mkdir(parents=True)
    pi.write_text(json.dumps({"providers": {connection_id: {"apiKey": "pi-secret"}}}))

    def read():
        return (engine.read_studio_api_key() if connection_id == "studio"
                else engine.read_studio_api_key(connection_id))

    assert read() == "global-secret"
    monkeypatch.delenv("TYLOG_LLM_KEY")
    assert read() == "connection-secret"
    monkeypatch.setenv(env_name, " ")
    assert read() == "local-secret"
    keys.write_text(json.dumps({connection_id: ""}))
    assert read() == "pi-secret"
    keys.write_text("invalid-local-secret")
    assert read() == "pi-secret"
    pi.write_text("invalid-pi-secret")
    with pytest.raises(RuntimeError, match="^Cannot read API key for LLM connection$"):
        read()
    assert "secret" not in caplog.text
