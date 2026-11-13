"""Persistent URLs rejected as junk."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .net import normalize_url

JUNK_LEDGER = Path(__file__).resolve().parents[2] / "junk_urls.jsonl"
_cache: dict[tuple[Path, int, int], set[str]] = {}


def _path(path=None) -> Path:
    return Path(path) if path is not None else Path(os.environ.get("ARTICLE_PIPELINE_JUNK_LEDGER", JUNK_LEDGER))


def load_junk(path=None) -> set[str]:
    path = _path(path)
    try:
        stat = path.stat()
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key not in _cache:
            urls = set()
            with path.open(encoding="utf-8", errors="replace") as ledger:
                for line in ledger:
                    try:
                        entry = json.loads(line)
                        if isinstance(entry, dict):
                            for field in ("normalized", "url"):
                                value = entry.get(field)
                                if isinstance(value, str) and value:
                                    urls.add(normalize_url(value))
                    except (ValueError, TypeError):
                        continue
            _cache.clear()
            _cache[key] = urls
        return _cache[key].copy()
    except FileNotFoundError:
        return set()


def is_junk_url(url: str) -> bool:
    urls = load_junk()
    return normalize_url(url) in urls or url in urls


def record_junk(url: str, reason: str, note_id: str = "") -> None:
    path = _path()
    urls = load_junk(path)
    if normalize_url(url) in urls or url in urls:
        return
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "url": url,
             "normalized": normalize_url(url), "reason": reason,
             "note_id": note_id, "source": "processor"}
    try:
        with path.open("rb") as existing:
            existing.seek(0, os.SEEK_END)
            size = existing.tell()
            if size:
                existing.seek(-1, os.SEEK_END)
            separator = "\n" if size and existing.read(1) != b"\n" else ""
    except FileNotFoundError:
        separator = ""
    with path.open("a", encoding="utf-8") as ledger:
        ledger.write(separator + json.dumps(entry, ensure_ascii=False) + "\n")
