"""Stateless scan of existing vault articles for related-note backlinks.

Reads only the tylog.note.with(...) header of each .typ file via regex —
mirrors TypstSeq's safe source parser, never depends on the app's _index/.
"""

import logging
import re
from pathlib import Path
from typing import List, NamedTuple

logger = logging.getLogger("article_pipeline")

_ID_RE = re.compile(r'^\s*id:\s*"((?:[^"\\]|\\.)*)"', re.M)
_TITLE_RE = re.compile(r'^\s*title:\s*"((?:[^"\\]|\\.)*)"', re.M)
_TAGS_RE = re.compile(r"^\s*tags:\s*\(([^)]*)\)", re.M)
_TAG_ITEM_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


class VaultNote(NamedTuple):
    id: str
    title: str
    tags: frozenset


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse_header(text: str) -> VaultNote | None:
    """Extract id/title/tags from the first tylog.note.with header block."""
    head = text[:4000]
    m_id = _ID_RE.search(head)
    m_title = _TITLE_RE.search(head)
    if not m_id or not m_title:
        return None
    m_tags = _TAGS_RE.search(head)
    tags = frozenset(
        _unescape(t) for t in _TAG_ITEM_RE.findall(m_tags.group(1))
    ) if m_tags else frozenset()
    return VaultNote(_unescape(m_id.group(1)), _unescape(m_title.group(1)), tags)


def scan_vault(articles_dir: Path, max_files: int = 5000) -> List[VaultNote]:
    notes: List[VaultNote] = []
    try:
        files = sorted(articles_dir.glob("*.typ"))
    except OSError:
        return notes
    if len(files) > max_files:
        logger.warning("Vault has %d files (> %d cap); skipping related scan", len(files), max_files)
        return notes
    for path in files:
        try:
            note = parse_header(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if note:
            notes.append(note)
    return notes


def find_related(
    tags: List[str], notes: List[VaultNote], exclude_id: str = "", max_links: int = 5
) -> List[VaultNote]:
    """Top-K vault notes by tag overlap with the new note's tags."""
    tag_set = set(tags)
    if not tag_set:
        return []
    scored = []
    for note in notes:
        if note.id == exclude_id:
            continue
        overlap = len(tag_set & note.tags)
        if overlap:
            scored.append((overlap, note))
    scored.sort(key=lambda x: (-x[0], x[1].title))
    return [note for _, note in scored[:max_links]]
