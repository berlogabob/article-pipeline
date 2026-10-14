"""Drain TyLog's article outbox using the existing article processor."""

import argparse
import fcntl
import json
import os
import plistlib
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from .config import Config, EngineConfig, load_config
from .engine import STUDIO_BASE_URL, STUDIO_PRIMARY_MODEL, STUDIO_FALLBACK_MODEL, make_engine
from .main import _project_root, _finalize_vault
from .processor import process_article
from .hub import sync_hub


def write_job(path: Path, job: dict) -> None:
    """Replace a complete JSON file so sync/readers never see a partial write."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".job-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(job, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def drain_jobs(cfg: Config, engine=None, connection_id: str = "studio") -> tuple[int, int]:
    vault = Path(cfg.typst.vault_dir).expanduser().resolve()
    jobs = vault / "_system/jobs/articles"
    jobs.mkdir(parents=True, exist_ok=True)
    done = errors = 0
    # This is a single-Mac worker; flock coordinates local instances, not other hosts.
    with (jobs / ".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0, 0
        for path in sorted(jobs.glob("*.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict):
                    raise ValueError("job must be an object")
            except (ValueError, UnicodeError):
                write_job(path, {"status": "error", "reason": "invalid_job_json"})
                errors += 1
                continue
            # A processing job under an unlocked local worker was interrupted.
            # Reuse processor dedup if it wrote the note before the interruption.
            if job.get("status") not in ("queued", "processing"):
                continue
            job.update(status="processing")
            job.pop("reason", None)
            job.pop("note_path", None)
            write_job(path, job)
            try:
                url = job.get("url")
                title = job.get("title") or ""
                if not isinstance(url, str) or not isinstance(title, str):
                    raise ValueError("invalid job")
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise ValueError("invalid URL")
                if engine is None:
                    engine = make_engine(cfg, connection_id=connection_id)
                paths = []
                ok, err = process_article(url, title, cfg, engine, result_paths=paths)
                if ok and paths:
                    job.update(status="done", note_path=paths[0].relative_to(vault).as_posix())
                    done += 1
                else:
                    job.update(status="error", reason=err.value if not ok else "note_path_missing")
                    errors += 1
            except Exception as exc:
                # Do not persist arbitrary exception text (may contain credentials).
                job.update(status="error", reason=f"worker_{type(exc).__name__}")
                errors += 1
            write_job(path, job)
        if done:
            _finalize_vault(cfg)
    return done, errors


def launchd_plist(root: Path, vault: Path, provider: str = "studio") -> bytes:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv must be installed to generate the launchd plist")
    jobs = vault / "_system/jobs/articles"
    return plistlib.dumps({
        "Label": "org.tylog.worker",
        "ProgramArguments": [uv, "run", "--project", str(root), "tylog-worker",
                             "--once", "--vault", str(vault), "--provider", provider],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "WatchPaths": [str(jobs)],
        "StartInterval": 60,
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    })


def _apply_ai_settings(cfg: Config) -> str | None:
    """Apply a valid synced article connection without reading any credentials."""
    try:
        path = Path(cfg.typst.vault_dir).expanduser() / "_system/settings/ai.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        if settings["schema"] != 1 or not isinstance(settings["connections"], list):
            return None
        task = settings["tasks"]["articles"]
        connection_id = task["connection"]
        if not isinstance(connection_id, str) or not connection_id.strip():
            return None
        connection = next(c for c in settings["connections"] if c["id"] == connection_id)
        url = connection["url"]
        models = (task.get("model"), task.get("fallback_model"))
        if not isinstance(url, str) or not url.strip():
            return None
        if any(model is not None and not isinstance(model, str) for model in models):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
        return None
    cfg.engine.base_url = url
    for field, model in zip(("primary_model", "fallback_model"), models):
        if model and model.strip():
            setattr(cfg.engine, field, model)
    return connection_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tylog-worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="drain pending jobs and exit (default)")
    mode.add_argument("--watch", action="store_true", help="poll for jobs continuously")
    parser.add_argument("--sync-hub", action="store_true", help="sync consultations and ideas")
    parser.add_argument("--vault", type=Path, help="override the configured TyLog vault")
    parser.add_argument("--provider", choices=["studio", "configured"], default="studio",
                        help="Studio defaults, or the existing config.yaml engine")
    parser.add_argument("--launchd-plist", type=Path, help="generate a plist at this path and exit")
    args = parser.parse_args(argv)
    root = _project_root()
    cfg = load_config(root) or Config()
    cfg.output_format = "typst"
    if args.vault:
        cfg.typst.vault_dir = str(args.vault.expanduser().resolve())
    if args.provider == "studio":
        cfg.engine = EngineConfig(provider="studio", base_url=STUDIO_BASE_URL,
                                  primary_model=STUDIO_PRIMARY_MODEL,
                                  fallback_model=STUDIO_FALLBACK_MODEL)
    elif cfg.engine.provider is None:
        parser.error("--provider configured requires an engine in config.yaml")
    connection_id = _apply_ai_settings(cfg)
    if args.launchd_plist:
        vault = Path(cfg.typst.vault_dir).expanduser().resolve()
        (vault / "_system/jobs/articles").mkdir(parents=True, exist_ok=True)
        args.launchd_plist.expanduser().write_bytes(launchd_plist(root, vault, args.provider))
        return 0
    try:
        next_hub = 0
        while True:
            hub_errors = 0
            if args.sync_hub or args.watch:
                if time.monotonic() >= next_hub:
                    try:
                        sync_hub(cfg)
                    except RuntimeError:
                        print("Hub sync failed")
                        hub_errors = 1
                    next_hub = time.monotonic() + 600
            if args.sync_hub and not (args.once or args.watch):
                return hub_errors
            done, errors = (drain_jobs(cfg, connection_id=connection_id)
                            if connection_id else drain_jobs(cfg))
            if done or errors or not args.watch:
                print(f"Jobs: {done} done, {errors} errors")
            if not args.watch:
                return 1 if errors or hub_errors else 0
            time.sleep(5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
