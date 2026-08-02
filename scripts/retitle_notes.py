"""Retitle notes whose title does not describe their content.

The vault's worst titles come from pages that name the *container*, not the
content: nine YouTube notes titled "url", tweet notes titled after the
account ("GitHub Projects Community GithubProjects on X"), Habr notes titled
"Хабр". The content itself is fine — the summary is already in the note — so
a new title is generated from the note's own body, no refetching.

Same contract as the tag scripts: a run writes a review file and changes
nothing; --apply reads the review file and rewrites. The review file is also
the undo log (old title, old filename).

    uv run python scripts/retitle_notes.py ~/Nextcloud/TyLogVault
    uv run python scripts/retitle_notes.py ~/Nextcloud/TyLogVault --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from article_pipeline.net import safe_filename  # noqa: E402
from article_pipeline.stage3_render_typst import (  # noqa: E402
    _typst_string,
    escape_markup,
)

GENERIC_TITLES = {
    "url",
    "хабр",
    "google search",
    "build software better, together",
    "javascript is not available.",
    "reddit post",
}

_SOCIAL_ACCOUNT_RE = re.compile(r".+ on (X|Threads)$")
_TITLE_LINE_RE = re.compile(r'^(  title: )"(?:[^"\\]|\\.)*",$', re.M)
_HEADING_RE = re.compile(r"^= .*$", re.M)
_PROP_RE = re.compile(r'"(title|source)": ')

MAX_TITLE_CHARS = 55


def parse_note(text: str) -> Dict[str, Optional[str]]:
    title_m = re.search(r'^  title: "((?:[^"\\]|\\.)*)",$', text, re.M)
    source_m = re.search(r'"source": "([^"]*)"', text)
    return {
        "title": title_m.group(1).replace('\\"', '"') if title_m else None,
        "source": source_m.group(1) if source_m else None,
    }


def bad_title_reason(title: Optional[str], source: Optional[str]) -> Optional[str]:
    if not title:
        return "empty"
    t = title.strip()
    if t.lower() in GENERIC_TITLES:
        return "generic"
    if source:
        domain = source.lower()
        if t.lower() in {domain, domain.split(".")[0]}:
            return "title-is-domain"
    if _SOCIAL_ACCOUNT_RE.match(t):
        return "account-name-only"
    if len(t) <= 4:
        return "too-short"
    return None


def summary_of(text: str, limit: int = 800) -> str:
    m = re.search(r"^== Summary$(.*?)(?=^==? )", text, re.M | re.S)
    body = m.group(1) if m else text.split("#line", 1)[-1]
    return " ".join(body.split())[:limit]


def propose_title(engine, model: str, note_text: str) -> Optional[str]:
    from article_pipeline.stage2_summarize import extract_json_object

    prompt = (
        "Below is a summary of a saved article or post. Write one specific,"
        f" informative title for it, at most {MAX_TITLE_CHARS} characters,"
        " in the same language as the summary. Name the actual subject, not"
        " the website or account it came from. Respond with JSON:"
        ' {"title": "..."}\n\nSummary:\n' + summary_of(note_text)
    )
    raw = engine.chat_json(prompt, model=model, temperature=0.05)
    title = str(extract_json_object(raw).get("title", "")).strip()
    if len(title) > MAX_TITLE_CHARS:
        # Cut at a word boundary, not mid-word.
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]
    return title.strip() or None


def new_filename(articles_dir: Path, source: Optional[str], title: str) -> str:
    stem = f"{source} - {title}" if source else title
    base = safe_filename(stem)
    name, n = f"{base}.typ", 2
    while (articles_dir / name).exists():
        name = f"{safe_filename(stem, max_chars=60 - len(f' ({n})'))} ({n}).typ"
        n += 1
    return name


def apply_one(articles_dir: Path, entry: Dict[str, str]) -> None:
    path = articles_dir / entry["file"]
    text = path.read_text(encoding="utf-8")
    new_title = entry["new_title"]
    text = _TITLE_LINE_RE.sub(
        lambda m: m.group(1) + _typst_string(new_title) + ",", text, count=1
    )
    text = _HEADING_RE.sub("= " + escape_markup(new_title), text, count=1)
    path.write_text(text, encoding="utf-8")
    path.rename(articles_dir / entry["new_file"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vault", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--review-file", type=Path, default=Path("retitle_review.json"))
    ap.add_argument("--limit", type=int, default=0, help="propose at most N (0 = all)")
    args = ap.parse_args()
    articles_dir = args.vault / "articles"

    if args.apply:
        entries = json.loads(args.review_file.read_text(encoding="utf-8"))
        for entry in entries:
            apply_one(articles_dir, entry)
            print(f"{entry['file']}  ->  {entry['new_file']}")
        print(f"applied {len(entries)}")
        return 0

    from article_pipeline.config import load_config
    from article_pipeline.engine import make_engine

    cfg = load_config(Path(__file__).resolve().parents[1])
    engine = make_engine(cfg)
    model = cfg.engine.primary_model

    entries: List[Dict[str, str]] = []
    for path in sorted(articles_dir.glob("*.typ")):
        text = path.read_text(encoding="utf-8")
        note = parse_note(text)
        reason = bad_title_reason(note["title"], note["source"])
        if not reason:
            continue
        title = propose_title(engine, model, text)
        if not title or bad_title_reason(title, note["source"]):
            print(f"skip (no usable title): {path.name}", file=sys.stderr)
            continue
        entries.append(
            {
                "file": path.name,
                "reason": reason,
                "old_title": note["title"] or "",
                "new_title": title,
                "new_file": new_filename(articles_dir, note["source"], title),
            }
        )
        print(f"[{len(entries)}] {path.name!r}: {note['title']!r} -> {title!r}")
        if args.limit and len(entries) >= args.limit:
            break

    args.review_file.write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n{len(entries)} proposals -> {args.review_file}; rerun with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
