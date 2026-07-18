"""Folder state machine: 01_inbox -> 02_processing -> 03_success / 04_failed."""

import logging
import time
from pathlib import Path
from typing import Tuple

from bs4 import BeautifulSoup

from .config import Config
from .errors import ProcessingError, validate_url
from .net import count_non_empty_lines, extract_links_from_markdown, move_to_folder
from .processor import process_article

logger = logging.getLogger("article_pipeline")

SKIP_PATTERNS = (".tmp", ".sync-conflict", ".crdownload", ".icloud", ".gitkeep", ".DS_Store")


def _error_suffix(error: ProcessingError) -> str:
    return f"_error_{error.value}_{int(time.time())}"


def parse_tabs_html(path: Path):
    links = []
    try:
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.startswith(("http://", "https://")) or not validate_url(href):
                continue
            text = a.get_text().strip() or Path(href).name or "Article"
            links.append((text, href))
    except Exception as e:
        logger.error("Error parsing %s: %s", path.name, e)
    return links


def recover_stale(root: Path, cfg: Config) -> int:
    """Move files stuck in 02_processing (crashed run) back to 01_inbox."""
    processing = root / cfg.folders.processing
    inbox = root / cfg.folders.inbox
    n = 0
    for f in processing.glob("*"):
        if f.is_file() and not any(p in f.name for p in SKIP_PATTERNS):
            move_to_folder(f, inbox)
            n += 1
    if n:
        logger.info("Recovered %d stale file(s) from %s", n, cfg.folders.processing)
    return n


def process_inbox_file(path: Path, root: Path, cfg: Config, engine, force: bool = False) -> bool:
    """Claim one inbox file, process it, land it in success/failed. Returns success."""
    success_dir = root / cfg.folders.success
    failed_dir = root / cfg.folders.failed

    claimed = move_to_folder(path, root / cfg.folders.processing)
    if claimed is None:
        return False
    path = claimed
    name_lower = path.name.lower()

    try:
        if name_lower.startswith("tabs") and name_lower.endswith(".html"):
            links = parse_tabs_html(path)
            if not links:
                move_to_folder(path, failed_dir, _error_suffix(ProcessingError.NO_URL_FOUND))
                return False
            ok = 0
            for title, url in links:
                success, err = process_article(url, title, cfg, engine, force=force)
                ok += 1 if success else 0
                logger.info("[tabs %d/%d] %s -> %s", ok, len(links), url[:60],
                            "ok" if success else err.value)
            move_to_folder(path, success_dir if ok else failed_dir)
            return ok > 0

        if name_lower.endswith(".md"):
            if count_non_empty_lines(path) == 0:
                move_to_folder(path, failed_dir, _error_suffix(ProcessingError.EMPTY_CONTENT))
                return False
            content = path.read_text(encoding="utf-8", errors="ignore")
            links = extract_links_from_markdown(content, fallback_title=path.stem)
            if not links:
                move_to_folder(path, failed_dir, _error_suffix(ProcessingError.NO_URL_FOUND))
                return False
            ok = 0
            for link in links:
                url = link["url"]
                title = link["title"]
                success, err = process_article(url, title, cfg, engine, force=force)
                ok += 1 if success else 0
                logger.info("[md %d/%d] %s -> %s", ok, len(links), url[:60],
                            "ok" if success else err.value)
            move_to_folder(path, success_dir if ok else failed_dir)
            return ok > 0

        logger.info("Unsupported file type, moving to failed: %s", path.name)
        move_to_folder(path, failed_dir, _error_suffix(ProcessingError.UNKNOWN))
        return False
    except Exception as e:
        logger.error("Unexpected error on %s: %s", path.name, e)
        move_to_folder(path, failed_dir, _error_suffix(ProcessingError.UNKNOWN))
        return False


def scan_inbox(root: Path, cfg: Config, engine, force: bool = False) -> Tuple[int, int]:
    """Process everything currently in the inbox. Returns (ok, failed)."""
    inbox = root / cfg.folders.inbox
    files = sorted(
        f for f in inbox.glob("*")
        if f.is_file() and not any(p in f.name for p in SKIP_PATTERNS)
    )
    ok = failed = 0
    for f in files:
        if process_inbox_file(f, root, cfg, engine, force=force):
            ok += 1
        else:
            failed += 1
    return ok, failed


def watch_inbox(root: Path, cfg: Config, engine, force: bool = False, poll_seconds: float = 2.0):
    """Watch 01_inbox with watchdog; process files as they appear."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    inbox = root / cfg.folders.inbox

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            self._maybe(event)

        def on_moved(self, event):
            self._maybe(event)

        def _maybe(self, event):
            if event.is_directory:
                return
            path = Path(getattr(event, "dest_path", "") or event.src_path)
            if any(p in path.name for p in SKIP_PATTERNS):
                return
            time.sleep(1.0)  # let the writer (browser/sync) finish
            if path.exists():
                process_inbox_file(path, root, cfg, engine, force=force)

    scan_inbox(root, cfg, engine, force=force)
    observer = Observer()
    observer.schedule(Handler(), str(inbox), recursive=False)
    observer.start()
    logger.info("Watching %s (Ctrl+C to stop)", inbox)
    try:
        while True:
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
