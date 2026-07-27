"""One-time migration: fold legacy reading-status properties onto TyLog's five stages.

Vault articles accumulated two competing claims about whether an article was
read — a bookkeeping `status` (often the pipeline's old "processed") and a
separate `read_status`. The app only ever reads `status`, and folds unknown
values through `articleStatusStage`, so the vault carried a vocabulary the app
had to keep translating.

This rewrites `status` to the further-along of the two stages and drops
`read_status`. Taking the *later* stage matters: real vault data disagrees in
both directions, and a naive "everything becomes unread" would walk genuinely
read articles backwards.

Mirrors `laterArticleStatusStage` in TypstSeq's lib/widgets/property_select_chip.dart.
Dry run by default; pass --apply to write.

    uv run python scripts/migrate_article_status.py ~/Nextcloud/TyLogVault
    uv run python scripts/migrate_article_status.py ~/Nextcloud/TyLogVault --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Reading-triage pipeline, in order. Index = how far along the article is.
STAGES = ["unread", "skimmed", "read", "extracted", "cited"]

# The header's properties dict is always serialized on a single line (verified
# across all 1678 vault articles), so this is a line-scoped edit and body text
# that happens to quote the same keys is never at risk.
_PROPERTIES_LINE = re.compile(r"^  properties: \(.*$", re.M)
_STATUS = re.compile(r'"status": "([^"]*)"')
_READ_STATUS = re.compile(r'"read_status": "([^"]*)", ?')


def stage(value: str | None) -> str:
    """Collapse any stored status onto one of the five stages.

    Must stay in step with articleStatusStage on the Dart side.
    """
    match value or "unread":
        case "unread" | "processed" | "":
            return "unread"
        case "skimmed":
            return "skimmed"
        case "extracted" | "summarized":
            return "extracted"
        case "cited":
            return "cited"
        case _:
            return "read"


def later_stage(a: str | None, b: str | None) -> str:
    left, right = stage(a), stage(b)
    return left if STAGES.index(left) >= STAGES.index(right) else right


def migrate_source(source: str) -> str:
    """Rewrite one note's properties line. Idempotent; returns source unchanged
    when there is nothing legacy to fold."""

    def rewrite(match: re.Match[str]) -> str:
        line = match.group(0)
        read_status = _READ_STATUS.search(line)
        status = _STATUS.search(line)
        # Nothing to do unless a read_status exists or status is the legacy
        # bookkeeping value. Notes whose status means something else entirely
        # ("ok", "success", "warning") are left alone.
        if not read_status and (not status or status.group(1) != "processed"):
            return line
        merged = later_stage(
            status.group(1) if status else None,
            read_status.group(1) if read_status else None,
        )
        line = _READ_STATUS.sub("", line, count=1)
        if status:
            # count=1 and anchored on the key: the properties dict also holds a
            # "processed": <date> *key*, which must never be touched.
            line = _STATUS.sub(f'"status": "{merged}"', line, count=1)
        else:
            line = line.replace("properties: (", f'properties: ("status": "{merged}", ', 1)
        return line

    # Only the header's properties line; body text is never touched.
    return _PROPERTIES_LINE.sub(rewrite, source, count=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, help="TyLog vault root")
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args(argv)

    articles = sorted((args.vault / "articles").glob("*.typ"))
    if not articles:
        print(f"no articles under {args.vault / 'articles'}", file=sys.stderr)
        return 1

    changed = []
    for path in articles:
        source = path.read_text(encoding="utf-8")
        migrated = migrate_source(source)
        if migrated == source:
            continue
        changed.append(path)
        if args.apply:
            path.write_text(migrated, encoding="utf-8")

    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb} {len(changed)} of {len(articles)} articles")
    if not args.apply:
        for path in changed[:5]:
            print(f"  {path.name}")
        if len(changed) > 5:
            print(f"  ... and {len(changed) - 5} more")
        print("\nre-run with --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
