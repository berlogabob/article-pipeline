"""Re-capture notes whose body is only Open Graph metadata.

A metadata-only note was a *success* — it never entered the failure ledger,
so --retry-failed cannot upgrade it, and process_article's id-dedup skips
it. Now that fuller capture exists (reader-first for social hosts), this
script closes the loop: each stub is moved aside, its URL re-processed with
force=True, and the stub deleted on success or restored on failure. The
note id derives from the URL, so the replacement keeps the same id and
asset folder — app links survive.

    uv run python scripts/reprocess_metadata_only.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from article_pipeline.config import load_config  # noqa: E402
from article_pipeline.engine import make_engine  # noqa: E402
from article_pipeline.logging_setup import setup_logging  # noqa: E402
from article_pipeline.processor import process_article  # noqa: E402


def stub_notes(articles_dir: Path) -> list[tuple[Path, str]]:
    """(path, url) for every metadata-only note in the vault."""
    found = []
    for path in sorted(articles_dir.glob("*.typ")):
        text = path.read_text(encoding="utf-8")
        if '"extraction": "metadata-only"' not in text:
            continue
        m = re.search(r'"url": "([^"]+)"', text)
        if m:
            found.append((path, m.group(1)))
    return found


def main() -> int:
    setup_logging()
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root)
    engine = make_engine(cfg)
    articles_dir = Path(cfg.typst.vault_dir).expanduser() / cfg.typst.articles_subdir

    if len(sys.argv) > 1:
        # Explicit note paths (e.g. notes that fail to compile) instead of
        # the metadata-only scan — same replace-or-restore contract.
        stubs = []
        for arg in sys.argv[1:]:
            path = Path(arg)
            m = re.search(r'"url": "([^"]+)"', path.read_text(encoding="utf-8"))
            if m:
                stubs.append((path, m.group(1)))
    else:
        stubs = stub_notes(articles_dir)
    print(f"{len(stubs)} notes to reprocess")
    upgraded = still_stub = failed = 0
    for path, url in stubs:
        backup = path.with_suffix(".typ.reprocessing")
        path.rename(backup)
        ok, err = process_article(url, path.stem, cfg, engine, force=True)
        if not ok:
            backup.rename(path)
            failed += 1
            print(f"  kept (reprocess failed: {err.value}): {path.name}")
            continue
        backup.unlink()
        # Did the recapture actually get further than metadata this time?
        fresh = stub_notes(articles_dir)
        if any(u == url for _, u in fresh):
            still_stub += 1
            print(f"  replaced, still metadata-only: {path.name}")
        else:
            upgraded += 1
            print(f"  upgraded: {path.name}")
    print(f"done: {upgraded} upgraded, {still_stub} still stubs, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
