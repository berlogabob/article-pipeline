from typing import List, Optional

from pydantic import BaseModel

FALLBACK_GUIDANCE = "(не удалось извлечь)"


class ArticleMetadata(BaseModel):
    summary_ru: str
    tags: List[str]
    author: Optional[str] = None
    verification_notes: str
    step_by_step_guidance: str = ""


def build_prompt(extracted_text: str, is_youtube: bool = False, max_chars: int = 8000) -> str:
    prompt = f"""Ты — точный аналитик. Верни ТОЛЬКО JSON без дополнительного текста.

Пример:
{{
  "summary_ru": "Краткое содержание...",
  "tags": ["тег1", "тег2"],
  "author": "Имя автора или null",
  "verification_notes": "Оценка...",
  "step_by_step_guidance": "1. Шаг...\n2. Шаг..."
}}

Текст:
{extracted_text[:max_chars]}
"""
    if is_youtube:
        prompt += "\nЭто YouTube видео. Обязательно сделай подробное step_by_step_guidance."
    return prompt


def create_fallback_metadata(url: str) -> ArticleMetadata:
    return ArticleMetadata(
        summary_ru=f"Ссылка: {url}\n(LLM не справился)",
        tags=["tabs-import"],
        verification_notes="Fallback",
        step_by_step_guidance=FALLBACK_GUIDANCE,
    )


def normalize_tag(tag: str) -> str:
    """Slugify a tag so identical concepts collapse to one backlink token."""
    return "-".join(tag.strip().lower().split())
