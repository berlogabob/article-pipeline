"""Drop failure-ledger entries whose URL already produced a vault article.

The 2026-08-19 backlog flush recovered ~1350 URLs but died before the
end-of-run ledger rewrite, so failures.jsonl still lists them as failed and
every retry pass re-fetches and re-summarizes work that is already done.

    uv run python scripts/prune_ledger.py ~/Nextcloud/TyLogVault
    uv run python scripts/prune_ledger.py ~/Nextcloud/TyLogVault --apply
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from article_pipeline.net import normalize_url
from article_pipeline.stage3_render_typst import note_id

_ID = re.compile(r'id: "(md-[0-9a-f]{16})"')
_URL = re.compile(r'"url": "([^"]+)"')


def vault_ids_and_urls(vault: Path) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    urls: set[str] = set()
    for f in (vault / "articles").glob("*.typ"):
        head = f.read_text(encoding="utf-8", errors="ignore")[:2000]
        if m := _ID.search(head):
            ids.add(m.group(1))
        if m := _URL.search(head):
            u = m.group(1).replace("\\/", "/")
            urls.add(u)
            try:
                urls.add(normalize_url(u))
            except ValueError:
                pass
    return ids, urls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vault", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    ledger = root / "failures.jsonl"
    ids, urls = vault_ids_and_urls(args.vault.expanduser())

    kept, dropped = [], 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        url = record.get("url", "")
        try:
            normalized = normalize_url(url)
        except ValueError:
            normalized = url
        if note_id(url) in ids or url in urls or normalized in urls:
            dropped += 1
        else:
            kept.append(line)

    print(f"{dropped} already in vault, {len(kept)} kept")
    if args.apply:
        ledger.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        print(f"wrote {ledger}")
    else:
        print("dry run; pass --apply to rewrite the ledger")


if __name__ == "__main__":
    main()
