"""Give unclustered articles an established tag they already imply.

An article clusters only if it carries a tag used on 5+ notes. Some articles
carry a *more specific* form of an established tag — `google-slides` where the
library uses `google`, `openai-api` where it uses `openai` — and so sit in
Uncategorized despite being squarely on a known topic.

This adds the established tag; it never removes or replaces one. `google-slides`
stays, `google` is added alongside. Nothing is lost and the article joins its
cluster.

The mapping is deliberately one-directional: a rare tag maps to an established
tag only when the rare tag *contains* it as a whole hyphen-segment, i.e. the
rare tag is the more specific of the two. The reverse (`development` ->
`ai-development`) would be inventing specificity the article never claimed, and
is exactly how a film article ends up in the AI cluster.

    uv run python scripts/remap_rare_tags.py ~/Nextcloud/TyLogVault
    uv run python scripts/remap_rare_tags.py ~/Nextcloud/TyLogVault --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set

from article_pipeline.tag_scan import read_index_bytes

MIN_NOTES = 5  # kConceptMapMinNotes in tylog_core/graph.dart

_HEADER_TAGS = re.compile(r"^(\s*tags:\s*)\((.*?)\)(,?\s*)$", re.M)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def normalize(tag: str) -> str:
    """Mirror _normalizeTag in scanner.dart."""
    return re.sub(r"[\s_]+", "-", str(tag).strip().lower()).strip("-")


def load_index(vault: Path) -> List[dict]:
    return json.loads(read_index_bytes(vault / "_index" / "index.json"))["notes"]


def established(notes: List[dict], min_notes: int = MIN_NOTES) -> Set[str]:
    counts: Counter[str] = Counter()
    for note in notes:
        for tag in note.get("tags") or []:
            counts[normalize(tag)] += 1
    return {t for t, n in counts.items() if n >= min_notes and t}


def implied_tag(tag: str, vocabulary: Set[str]) -> Optional[str]:
    """The established tag `tag` is a more specific form of, if any.

    Segment-wise containment only. Substring matching would map `rust` onto
    `trust` and `ai` onto `air-quality`.
    """
    tag = normalize(tag)
    if tag in vocabulary:
        return None  # already clusters; nothing to add
    segments = tag.split("-")
    best: Optional[str] = None
    for candidate in vocabulary:
        parts = candidate.split("-")
        if len(parts) > len(segments):
            continue
        # candidate must appear as a contiguous run of whole segments
        if any(
            segments[i : i + len(parts)] == parts
            for i in range(len(segments) - len(parts) + 1)
        ):
            # Prefer the most specific established tag that fits.
            if best is None or len(candidate) > len(best):
                best = candidate
    return best


def additions_for(note: dict, vocabulary: Set[str]) -> List[str]:
    tags = {normalize(t) for t in (note.get("tags") or [])}
    if tags & vocabulary:
        return []  # already clustered
    found = {implied_tag(t, vocabulary) for t in tags}
    return sorted(t for t in found if t)


def add_header_tags(source: str, add: List[str]) -> str:
    """Merge `add` into the note header's `tags: (...)` list."""
    match = _HEADER_TAGS.search(source)
    if match is None:
        return source
    existing = [m.group(1) for m in _QUOTED.finditer(match.group(2))]
    merged = existing + [t for t in add if t not in existing]
    rendered = "()" if not merged else "(" + ", ".join(f'"{t}"' for t in merged) + ",)"
    return source[: match.start()] + match.group(1) + rendered + match.group(3) + source[match.end() :]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path)
    parser.add_argument("--apply", action="store_true", help="write changes")
    parser.add_argument("--min-notes", type=int, default=MIN_NOTES)
    args = parser.parse_args(argv)

    vault = args.vault.expanduser()
    try:
        notes = load_index(vault)
    except (OSError, ValueError) as e:
        print(f"cannot read {vault}/_index/index.json: {e}", file=sys.stderr)
        return 1

    vocabulary = established(notes, args.min_notes)
    articles = [n for n in notes if n.get("kind") == "article"]
    planned: Dict[str, List[str]] = {}
    for note in articles:
        add = additions_for(note, vocabulary)
        if add:
            planned[note["path"]] = add

    print(f"vocabulary: {len(vocabulary)} tags on >={args.min_notes} notes")
    print(f"{'would add' if not args.apply else 'added'} tags to {len(planned)} articles")

    changed = 0
    for path, add in sorted(planned.items()):
        file = vault / path
        try:
            source = file.read_text(encoding="utf-8")
        except OSError:
            continue
        updated = add_header_tags(source, add)
        if updated == source:
            continue
        changed += 1
        if args.apply:
            file.write_text(updated, encoding="utf-8")
        elif changed <= 8:
            print(f"  {Path(path).name[:56]:<58} +{', '.join(add)}")

    if not args.apply:
        if changed > 8:
            print(f"  ... and {changed - 8} more")
        print("\nre-run with --apply to write, then rebuild the index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
