"""CLI entry point: run once, --watch, --reconfigure."""

import argparse
import sys
from pathlib import Path

from .failures import LEDGER_NAME


def _project_root() -> Path:
    # src/article_pipeline/main.py -> project root
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="article-pipeline",
        description="Process article URLs from 01_inbox into Logseq markdown or TyLog Typst notes.",
    )
    parser.add_argument("--watch", action="store_true", help="keep watching 01_inbox for new files")
    parser.add_argument("--reconfigure", action="store_true", help="re-run the setup wizard")
    parser.add_argument("--force", action="store_true", help="reprocess URLs even if already saved")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=f"re-run the retryable URLs in {LEDGER_NAME} and drop the ones that succeed",
    )
    parser.add_argument("--debug", action="store_true", help="debug logging")
    args = parser.parse_args()

    root = _project_root()

    from .config import load_config
    from .logging_setup import setup_logging
    from .wizard import run_wizard

    cfg = None if args.reconfigure else load_config(root)
    if cfg is None:
        cfg = run_wizard(root)

    setup_logging(
        level="DEBUG" if args.debug else cfg.logging.level,
        folder=cfg.logging.folder,
        console=cfg.logging.console,
    )

    from .engine import make_engine
    from .failures import load_failures, retry_failures, summarize_failures
    from .watch import recover_stale, scan_inbox, watch_inbox

    engine = make_engine(cfg)

    if args.retry_failed:
        recovered, remaining = retry_failures(root, cfg, engine, force=args.force)
        print(f"Retried: {recovered} recovered, {remaining} still failing")
        return 0

    recover_stale(root, cfg)

    if args.watch:
        watch_inbox(root, cfg, engine, force=args.force)
        return 0

    ok, failed = scan_inbox(root, cfg, engine, force=args.force)
    print(f"Done: {ok} ok, {failed} failed")
    outstanding = load_failures(root)
    if outstanding:
        # The file-level tally above hides per-URL failures inside files that
        # otherwise succeeded, so say what is actually outstanding.
        print(f"{len(outstanding)} URLs in {LEDGER_NAME}: {summarize_failures(outstanding)}")
        print("  re-run the retryable ones with --retry-failed")
    return 1 if (failed and not ok) else 0


if __name__ == "__main__":
    sys.exit(main())
