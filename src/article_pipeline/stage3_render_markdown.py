"""Render an article as an Obsidian/Logseq markdown note with YAML frontmatter.

Format ported from the restored oMLX variant (its newest markdown contract):
YAML frontmatter + `# title` + ## Summary / ## Step-by-step guidance /
## Verification sections + full extracted text.
"""

from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import yaml

from .metadata import ArticleMetadata, FALLBACK_GUIDANCE, normalize_tags


def source_url_for_domain(source: Optional[str], url: str) -> str:
    domain = source or urlparse(url).netloc.lower().removeprefix("www.")
    if not domain:
        return url
    return f"https://{domain}"


def build_frontmatter(
    title: str,
    url: str,
    metadata: ArticleMetadata,
    source: Optional[str] = None,
    journal_day: Optional[str] = None,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    frontmatter = {
        "title": title,
        "aliases": [],
        "tags": normalize_tags(metadata.tags),
        "type": "article",
        "journal_day": journal_day or now,
        "status": "processed",
        "read_status": "unread",
        "processed": now,
        "created": now,
        "url": url,
        "source": source,
        "source_url": source_url_for_domain(source, url),
        "author": metadata.author,
    }
    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n"


def build_content(
    title: str,
    url: str,
    metadata: ArticleMetadata,
    extracted_text: str,
    is_youtube: bool = False,
    source: Optional[str] = None,
    max_len: Optional[int] = None,  # accepted for call compatibility; full text is kept
) -> str:
    if source is None and is_youtube:
        source = "youtube"
    frontmatter = build_frontmatter(title, url, metadata, source=source)

    guidance = (metadata.step_by_step_guidance or "").strip()
    show_guidance = bool(metadata.is_tutorial and guidance and guidance != FALLBACK_GUIDANCE)
    guidance_block = f"\n## Step-by-step guidance\n{guidance}\n" if show_guidance else ""

    return f"""{frontmatter}# {title}

## Summary
{metadata.summary_ru.strip()}
{guidance_block}
## Verification
{metadata.verification_notes.strip()}

---

{extracted_text.strip()}
"""
