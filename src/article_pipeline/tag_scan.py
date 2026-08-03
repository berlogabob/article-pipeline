"""Stateless scan of existing vault articles for related-note backlinks.

Reads only the tylog.note.with(...) header of each .typ file via regex —
mirrors TypstSeq's safe source parser, never depends on the app's _index/.
"""

import json
import logging
import re
from collections import Counter
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


def _index_notes(vault_dir: Path) -> List[dict]:
    """Notes from the vault index, or from a donor when the index is absent.

    The index is rewritten only when a scan finishes, so it can be missing for
    minutes during a rebuild — and a run in that window would otherwise get an
    empty vocabulary and quietly mint the singleton tags the vocabulary exists
    to prevent. Donors carry the same note list and survive the gap.
    """
    for candidate in [vault_dir / "_index" / "index.json", *sorted((vault_dir / "_system" / "index").glob("*.json"))]:
        try:
            notes = json.loads(candidate.read_text(encoding="utf-8")).get("notes")
        except (OSError, ValueError):
            continue
        if notes:
            if candidate.name != "index.json":
                logger.info("Index unavailable; taking tag vocabulary from %s", candidate.name)
            return notes
    logger.warning("No usable tag vocabulary under %s", vault_dir)
    return []


def vocabulary(vault_dir: Path, min_notes: int = 5, limit: int = 400) -> List[str]:
    """The vault's established tags, most-used first.

    Read from `_index/index.json` rather than by scanning headers, because the
    tags that matter most are not in the headers: articles imported from Logseq
    carry `tags: ()` and keep their real tags on a body `tags::` line that only
    TyLog's scanner parses. Header scanning would return a vocabulary missing
    hundreds of notes' worth of tags.

    `min_notes` mirrors kConceptMapMinNotes in tylog_core/graph.dart — a tag
    below it does not cluster anything, so proposing it to the model would just
    mint another singleton.
    """
    notes = _index_notes(vault_dir)
    if not notes:
        return []
    counts: Counter[str] = Counter()
    for note in notes:
        for tag in note.get("tags") or []:
            tag = str(tag).strip().lower()
            if tag:
                counts[tag] += 1
    established = [t for t, n in counts.most_common() if n >= min_notes]
    return established[:limit]


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
