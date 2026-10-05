import fcntl
import json
import plistlib

import httpx
import pytest

from article_pipeline import engine, processor, worker
from article_pipeline.config import Config, EngineConfig
from article_pipeline.errors import ProcessingError


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cfg = Config(output_format="typst", engine=EngineConfig(
        provider="studio", primary_model=engine.STUDIO_PRIMARY_MODEL,
        fallback_model=engine.STUDIO_FALLBACK_MODEL))
    cfg.typst.vault_dir = str(tmp_path)
    cfg.typst.download_images = False
    cfg.llm.temperature_attempts = [0.1]
    monkeypatch.setattr(worker, "_finalize_vault", lambda cfg: None)
    monkeypatch.setattr(processor, "ingest_article", lambda *a, **kw: ({
        "expanded_url": "https://example.com/article", "normalized_url": "https://example.com/article",
        "resolved_title": "An Article", "extracted_text": "Article body text.",
        "image_urls": [], "is_youtube": False, "source": "example.com",
    }, ProcessingError.UNKNOWN))
    return cfg


def job_path(cfg, name="job.json", **fields):
    path = worker.Path(cfg.typst.vault_dir) / "_system/jobs/articles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    worker.write_job(path, {"url": "https://example.com/article", "shared_at": "2026-10-05",
                            "status": "queued", **fields})
    return path


class FakeLLM:
    def __init__(self, path, fail=False):
        self.path, self.fail, self.models = path, fail, []

    def chat_json(self, prompt, model, temperature):
        assert json.loads(self.path.read_text())["status"] == "processing"
        self.models.append(model)
        if self.fail or model == engine.STUDIO_PRIMARY_MODEL:
            raise RuntimeError("mock failure")
        return json.dumps({"summary_ru": "Краткое содержание", "tags": ["python"],
                           "author": "Author", "verification_notes": "Verified",
                           "is_tutorial": False, "step_by_step_guidance": None})


def test_real_stages_fallback_and_retry_dedup(cfg):
    path = job_path(cfg, title="Shared title")
    llm = FakeLLM(path)
    assert worker.drain_jobs(cfg, llm) == (1, 0)
    job = json.loads(path.read_text())
    assert job["status"] == "done" and job["shared_at"] == "2026-10-05"
    note = worker.Path(cfg.typst.vault_dir) / job["note_path"]
    assert note.suffix == ".typ"
    content = note.read_text()
    assert "An Article" in content and "Article body text." in content and "Краткое содержание" in content
    assert llm.models == [engine.STUDIO_PRIMARY_MODEL, engine.STUDIO_FALLBACK_MODEL]
    assert worker.drain_jobs(cfg, llm) == (0, 0)
    worker.write_job(path, {**job, "status": "processing"})
    cfg.typst.related_articles.max_vault_files_scanned = 0
    assert worker.drain_jobs(cfg, llm) == (1, 0)
    assert json.loads(path.read_text())["note_path"] == job["note_path"]
    assert len(list(note.parent.glob("*.typ"))) == 1
    assert len(llm.models) == 2


def test_llm_failure_no_note_and_explicit_retry(cfg):
    path = job_path(cfg)
    assert worker.drain_jobs(cfg, FakeLLM(path, fail=True)) == (0, 1)
    assert json.loads(path.read_text())["reason"] == "llm_error"
    assert not list(worker.Path(cfg.typst.vault_dir).rglob("*.typ"))
    assert worker.drain_jobs(cfg, FakeLLM(path)) == (0, 0)
    worker.write_job(path, {**json.loads(path.read_text()), "status": "queued"})
    assert worker.drain_jobs(cfg, FakeLLM(path)) == (1, 0)
    assert "reason" not in json.loads(path.read_text())


def test_invalid_jobs_do_not_stop_drain(cfg):
    path = job_path(cfg, url="file:///etc/passwd")
    broken = job_path(cfg, name="broken.json")
    broken.write_text("{")
    assert worker.drain_jobs(cfg, object()) == (0, 2)
    assert json.loads(path.read_text())["status"] == "error"
    assert json.loads(broken.read_text())["reason"] == "invalid_job_json"


def test_local_claim_lock(cfg):
    path = job_path(cfg)
    with (path.parent / ".worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.drain_jobs(cfg, FakeLLM(path)) == (0, 0)
    assert json.loads(path.read_text())["status"] == "queued"


def test_studio_runtime_key_and_sanitized_errors(tmp_path, monkeypatch, caplog):
    settings = tmp_path / "models.json"
    monkeypatch.setattr(engine, "STUDIO_SETTINGS_PATH", settings)
    seen = []
    def handler(request):
        seen.append(request.headers["Authorization"])
        payload = json.loads(request.content)
        assert "response_format" not in payload
        assert request.url == "http://desktop-vdsrh2e:8888/v1/chat/completions"
        if len(seen) == 2:
            raise RuntimeError("secret-two response body")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    client = engine.make_engine(Config(engine=EngineConfig(provider="studio")))
    client._transport = httpx.MockTransport(handler)
    settings.write_text(json.dumps({"providers": {"studio": {"apiKey": "secret-one"}}}))
    assert client.chat_json("prompt", "model", 0) == "{}"
    settings.write_text(json.dumps({"providers": {"studio": {"apiKey": "secret-two"}}}))
    with pytest.raises(RuntimeError, match="^Studio request failed$"):
        client.chat_json("prompt", "model", 0)
    assert seen == ["Bearer secret-one", "Bearer secret-two"]
    assert "secret" not in caplog.text
    settings.write_text("invalid-secret")
    with pytest.raises(RuntimeError, match="Cannot read"):
        engine.read_studio_api_key()


def test_cli_and_plist(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "load_config", lambda root: cfg.model_copy(deep=True))
    monkeypatch.setattr(worker.shutil, "which", lambda name: "/opt/homebrew/bin/uv")
    plist = tmp_path / "worker.plist"
    assert worker.main(["--launchd-plist", str(plist), "--vault", str(tmp_path)]) == 0
    data = plistlib.loads(plist.read_bytes())
    assert data["WatchPaths"] == [str(tmp_path / "_system/jobs/articles")]
    assert "--once" in data["ProgramArguments"] and data["StartInterval"] == 60
    assert "apiKey" not in plist.read_text()
    observed = []
    monkeypatch.setattr(worker, "drain_jobs", lambda config: (observed.append(config) or (0, 0)))
    assert worker.main(["--once"]) == 0
    assert observed[-1].engine.primary_model == engine.STUDIO_PRIMARY_MODEL
    cfg.engine = EngineConfig(provider="claude", primary_model="haiku")
    assert worker.main(["--once", "--provider", "configured"]) == 0
    assert observed[-1].engine.provider == "claude"
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert worker.main(["--watch"]) == 0
