"""Find junk articles in the vault (error pages, link shells, thin stubs, dups).

Two-step, review-first:

    uv run python scripts/audit_junk.py ~/Nextcloud/TyLogVault
        -> writes junk_review.json next to this repo's failures.jsonl.
           Every candidate has "delete": true prefilled; flip to false to keep.

    uv run python scripts/audit_junk.py ~/Nextcloud/TyLogVault --apply
        -> deletes files still marked "delete": true in junk_review.json,
           removes their assets/articles/<id>/ dirs, and strips dangling
           #tylog.ref-note("<deleted id>") lines from surviving notes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from article_pipeline.processor import junk_reason

_ID = re.compile(r'id: "(md-[0-9a-f]{16})"')
_URL = re.compile(r'"url": "([^"]+)"')
_TITLE = re.compile(r'title: "((?:[^"\\]|\\.)*)"')
_LOW_VERIFICATION = re.compile(r"== Verification\s+низк", re.U)


def scan(vault: Path) -> tuple[list[dict], list[dict]]:
    candidates: list[dict] = []
    by_url: dict[str, list[str]] = defaultdict(list)
    for f in sorted((vault / "articles").glob("*.typ")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        head = text[:2000]
        nid = m.group(1) if (m := _ID.search(head)) else ""
        url = (m.group(1).replace("\\/", "/") if (m := _URL.search(head)) else "")
        title = m.group(1) if (m := _TITLE.search(head)) else f.stem
        if url:
            by_url[url].append(f.name)

        # Body length: everything after the closing of the #show header.
        body = text.split(")\n", 1)[-1]
        reason = junk_reason(title, url)
        # Short-form sources are legitimately tiny (metadata-only YouTube
        # notes, posts) — size heuristics only apply off these hosts.
        host = urlparse(url).netloc.lower().removeprefix("www.")
        shortform = host in {
            "youtube.com", "youtu.be", "x.com", "twitter.com",
            "instagram.com", "reddit.com", "t.me",
        }
        if not reason and "share.google" in url and len(body.strip()) < 2500:
            # A real article fetched through the interstitial has a full
            # body; the failed ones hold only the interstitial's own text.
            reason = "share.google interstitial, no real content"
        if not reason and not shortform:
            if len(body.strip()) < 600:
                reason = f"near-empty body ({len(body.strip())} chars)"
            elif _LOW_VERIFICATION.search(text) and len(body.strip()) < 1500:
                reason = "verification=низкая and near-empty body"
        if reason:
            candidates.append(
                {"file": f.name, "id": nid, "url": url, "reason": reason, "delete": True}
            )

    dups = [
        {"url": u, "files": names, "keep": names[-1]}
        for u, names in sorted(by_url.items())
        if len(names) > 1
    ]
    return candidates, dups


def apply_review(vault: Path, review: dict) -> None:
    articles = vault / "articles"
    doomed = [c for c in review["candidates"] if c.get("delete")]
    # Duplicate handling: delete every file in the group except "keep".
    for d in review.get("duplicates", []):
        for name in d["files"]:
            if name != d.get("keep"):
                doomed.append({"file": name, "id": "", "url": d["url"], "reason": "duplicate"})

    deleted_ids: set[str] = set()
    for c in doomed:
        path = articles / c["file"]
        if not path.exists():
            continue
        head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
        if m := _ID.search(head):
            deleted_ids.add(m.group(1))
        path.unlink()
        assets = vault / "assets" / "articles" / (c.get("id") or (m.group(1) if m else ""))
        if c.get("id") or m:
            shutil.rmtree(assets, ignore_errors=True)
    print(f"deleted {len(doomed)} notes")

    if not deleted_ids:
        return
    ref = re.compile(
        r'^.*#tylog\.ref-note\("(' + "|".join(map(re.escape, deleted_ids)) + r')"\).*\n',
        re.M,
    )
    fixed = 0
    for f in articles.glob("*.typ"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        new = ref.sub("", text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            fixed += 1
    print(f"stripped dangling refs in {fixed} notes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vault", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    vault = args.vault.expanduser()
    review_path = Path(__file__).resolve().parent.parent / "junk_review.json"

    if args.apply:
        apply_review(vault, json.loads(review_path.read_text(encoding="utf-8")))
        return

    candidates, dups = scan(vault)
    review_path.write_text(
        json.dumps(
            {"candidates": candidates, "duplicates": dups},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(candidates)} junk candidates, {len(dups)} duplicate-URL groups")
    print(f"review {review_path}, flip \"delete\" to false to keep, then --apply")


if __name__ == "__main__":
    main()
