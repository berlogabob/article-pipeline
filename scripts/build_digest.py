"""Build a weekly digest note: what arrived this week, grouped by tag.

Reads the vault index (no LLM calls — titles and tags already exist),
collects articles whose `processed` date falls in the target ISO week, and
writes one `notes/<year>-W<week> digest.typ` with a clickable
`tylog.ref-note` line per article. Re-running a week overwrites its digest.

    uv run python scripts/build_digest.py ~/Nextcloud/TyLogVault
    uv run python scripts/build_digest.py ~/Nextcloud/TyLogVault --week 2026-W31
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from article_pipeline.stage3_render_typst import (  # noqa: E402
    _typst_dict,
    _typst_list,
    _typst_string,
    escape_markup,
)
from article_pipeline.tag_scan import read_index_bytes  # noqa: E402

OTHER = "Прочее / Other"


def week_bounds(week: str) -> tuple[dt.date, dt.date]:
    """ISO week 'YYYY-Www' -> (monday, sunday)."""
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    return monday, monday + dt.timedelta(days=6)


def articles_for_week(index: dict, week: str) -> List[dict]:
    start, end = week_bounds(week)
    picked = []
    for note in index["notes"]:
        if note.get("kind") != "article":
            continue
        processed = (note.get("properties") or {}).get("processed", "")
        try:
            day = dt.date.fromisoformat(processed)
        except ValueError:
            continue
        if start <= day <= end:
            picked.append(note)
    return picked


def group_by_tag(articles: List[dict], vocab: List[str]) -> Dict[str, List[dict]]:
    """Each article files under its highest-ranked established tag."""
    rank = {t: i for i, t in enumerate(vocab)}
    groups: Dict[str, List[dict]] = defaultdict(list)
    for a in articles:
        tags = [t for t in (a.get("tags") or []) if t in rank]
        groups[min(tags, key=lambda t: rank[t]) if tags else OTHER].append(a)
    return dict(groups)


def render_digest(week: str, groups: Dict[str, List[dict]]) -> str:
    total = sum(len(v) for v in groups.values())
    title = f"Digest {week}"
    header = (
        '#import "/_system/tylog.typ" as tylog\n\n'
        "#show: tylog.note.with(\n"
        f"  id: {_typst_string('digest-' + week.lower())},\n"
        f"  title: {_typst_string(title)},\n"
        '  kind: "note",\n'
        f"  date: {_typst_string(week_bounds(week)[1].isoformat())},\n"
        f"  tags: {_typst_list(['digest'])},\n"
        "  aliases: (),\n"
        "  project: none,\n"
        f"  properties: {_typst_dict({'week': week, 'articles': str(total)})},\n"
        ")\n"
    )
    lines = [header, "= " + escape_markup(title), ""]
    # Big groups first; Other always last.
    ordered = sorted(groups, key=lambda g: (g == OTHER, -len(groups[g]), g))
    for group in ordered:
        lines.append("== " + escape_markup(group))
        lines.append("")
        for a in sorted(groups[group], key=lambda a: a["title"].lower()):
            source = (a.get("properties") or {}).get("source", "")
            suffix = f" — {escape_markup(source)}" if source else ""
            lines.append(
                f"- #tylog.ref-note({_typst_string(a['id'])})"
                f"[{escape_markup(a['title'])}]{suffix}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vault", type=Path)
    ap.add_argument("--week", help="ISO week YYYY-Www (default: current)")
    args = ap.parse_args()

    today = dt.date.today()
    week = args.week or f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"

    index = json.loads(read_index_bytes(args.vault / "_index" / "index.json"))
    articles = articles_for_week(index, week)
    if not articles:
        print(f"no articles processed in {week}; nothing written")
        return 0

    from article_pipeline.tag_scan import vocabulary

    groups = group_by_tag(articles, vocabulary(args.vault))
    out = args.vault / "notes" / f"{week} digest.typ"
    out.write_text(render_digest(week, groups), encoding="utf-8")
    print(f"{out.name}: {len(articles)} articles in {len(groups)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
