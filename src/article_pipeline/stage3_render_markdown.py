from datetime import datetime
from typing import Optional

from article_pipeline.metadata import ArticleMetadata, FALLBACK_GUIDANCE


def build_props(
    title: str,
    url: str,
    res: ArticleMetadata,
    source: Optional[str] = None,
    journal_day: Optional[str] = None,
) -> str:
    lines = [f"title:: {title}"]

    if res.tags:
        lines.append(f"tags:: {' '.join(f'[[{t}]]' for t in res.tags)}")

    now = datetime.now().strftime("%Y-%m-%d")
    if not journal_day:
        journal_day = now

    lines.extend(
        [
            "type:: article",
            f"journal-day:: [[{journal_day}]]",
            "status:: processed",
            f"processed:: {now}",
            f"created:: {now}",
            f"url:: {url}",
        ]
    )

    if source:
        lines.append(f"source:: [[{source.strip()}]]")

    if res.author:
        lines.append(f"author:: {res.author}")

    return "\n".join(lines) + "\n\n"


def build_content(
    title: str,
    url: str,
    metadata: ArticleMetadata,
    extracted_text: str,
    is_youtube: bool = False,
    max_len: int = 8000,
) -> str:
    source = "youtube" if is_youtube else None
    props = build_props(title, url, metadata, source=source)

    source_label = " (по видео)" if is_youtube else ""

    text_preview = (
        extracted_text.strip()
        if len(extracted_text) < max_len
        else extracted_text[:max_len] + "\n... (полный текст ниже)"
    )

    guidance = (metadata.step_by_step_guidance or "").strip()
    show_guidance = bool(guidance and guidance != FALLBACK_GUIDANCE)

    guidance_block = ""
    if show_guidance:
        guidance_block = f"""
**Шаг за шагом руководство**{source_label}
{guidance}
"""

    content = f"""{props}
**Summary**
{metadata.summary_ru.strip()}

{guidance_block}
**Достоверность**
{metadata.verification_notes.strip()}

---
{text_preview}
"""
    return content
