"""An append-only record of every URL that failed, and a way to re-run them.

The folder stages say what happened to a *file*; they cannot say what happened
to a *URL*. That gap mattered: a file counts as a success if any one of its
links worked, so in a ten-link file nine failures left no trace outside the
log. And of the 23 files sitting in 04_failed/, 22 carried no error suffix at
all, so even the file-level verdict was mute about why.

One JSONL line per failed URL closes both. No queue, no database — the ledger
is rewritten on retry, keeping only what still fails, so it drains itself.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, TypedDict

from .errors import ProcessingError, is_retryable

logger = logging.getLogger("article_pipeline")

LEDGER_NAME = "failures.jsonl"


class FailureRecord(TypedDict):
    ts: str
    source_file: str
    url: str
    title: str
    error: str
    retryable: bool


def ledger_path(root: Path) -> Path:
    return root / LEDGER_NAME


def record_failure(
    root: Path,
    *,
    source_file: str,
    url: str,
    title: str,
    error: ProcessingError,
) -> None:
    """Append one failed URL. Never raises: diagnostics must not stop a run."""
    record: FailureRecord = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": source_file,
        "url": url,
        "title": title,
        "error": error.value,
        "retryable": is_retryable(error),
    }
    try:
        with ledger_path(root).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Could not record failure for %s: %s", url[:60], e)


def load_failures(root: Path) -> List[FailureRecord]:
    """Every recorded failure, newest occurrence per URL winning.

    A URL that failed, was retried, and failed again should appear once with
    its latest error, not once per attempt.
    """
    path = ledger_path(root)
    if not path.exists():
        return []
    by_url: dict[str, FailureRecord] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line from a killed run
            if isinstance(record, dict) and record.get("url"):
                by_url[record["url"]] = record
    except OSError as e:
        logger.warning("Could not read %s: %s", path, e)
        return []
    return list(by_url.values())


def retryable_failures(root: Path) -> List[FailureRecord]:
    return [r for r in load_failures(root) if r.get("retryable")]


def rewrite_ledger(root: Path, records: Iterable[FailureRecord]) -> None:
    """Replace the ledger with `records`. Empty means delete the file."""
    path = ledger_path(root)
    records = list(records)
    try:
        if not records:
            path.unlink(missing_ok=True)
            return
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Could not rewrite %s: %s", path, e)


def summarize_failures(records: Iterable[FailureRecord]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.get("error", "unknown")] = (
            counts.get(record.get("error", "unknown"), 0) + 1
        )
    if not counts:
        return "no recorded failures"
    return ", ".join(f"{n} {err}" for err, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def retry_failures(root, cfg, engine, *, force: bool = False) -> tuple[int, int]:
    """Re-run every retryable URL; drop the ones that now succeed.

    Terminal failures are carried through untouched — a 404 will not become a
    200, and re-requesting a URL that has already refused us is just rude.
    Returns (recovered, still_failing).
    """
    from .processor import process_article  # imported late: processor imports us

    all_records = load_failures(root)
    retryable = [r for r in all_records if r.get("retryable")]
    terminal = [r for r in all_records if not r.get("retryable")]
    if not retryable:
        logger.info("Nothing retryable in the ledger (%s)", summarize_failures(all_records))
        return 0, len(terminal)

    logger.info("Retrying %d failed URLs (%s)", len(retryable), summarize_failures(retryable))
    still_failing: List[FailureRecord] = []
    recovered = 0
    for i, record in enumerate(retryable):
        url = record["url"]
        ok, err = process_article(url, record.get("title") or url, cfg, engine, force=force)
        if ok:
            recovered += 1
            logger.info("Recovered: %s", url[:70])
        else:
            still_failing.append({**record, "error": err.value, "retryable": is_retryable(err)})
            logger.info("Still failing (%s): %s", err.value, url[:70])
        # A pass over thousands of URLs runs for hours; rewriting only at the
        # end means a crash forgets everything and the next pass redoes the
        # whole list (which is exactly what happened on 2026-08-19).
        # ponytail: checkpoint every 20 — a crash loses at most 20 URLs of progress
        if (i + 1) % 20 == 0:
            rewrite_ledger(root, terminal + still_failing + retryable[i + 1 :])

    rewrite_ledger(root, terminal + still_failing)
    return recovered, len(terminal) + len(still_failing)
