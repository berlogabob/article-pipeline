import re
from typing import List, Optional

from pydantic import BaseModel

FALLBACK_GUIDANCE = "(не удалось извлечь)"

# keys the LLM must return; used to pick the right JSON object out of chatty output
METADATA_JSON_KEYS = {
    "summary_ru",
    "tags",
    "author",
    "verification_notes",
    "is_tutorial",
    "step_by_step_guidance",
}


class ArticleMetadata(BaseModel):
    summary_ru: str
    tags: List[str]
    author: Optional[str] = None
    verification_notes: str
    is_tutorial: bool = False
    step_by_step_guidance: Optional[str] = None


def build_prompt(extracted_text: str, is_youtube: bool = False, max_chars: int = 8000) -> str:
    source_hint = (
        "Это YouTube материал. Определи, является ли он обучающим туториалом."
        if is_youtube
        else "Это веб-статья. Не считай её туториалом без явных пошаговых действий."
    )
    return f"""/no_think
Ты — точный аналитик для личной библиотеки знаний.
Верни только один валидный JSON object. Первый символ ответа должен быть {{, последний символ должен быть }}.
Не показывай ход рассуждений, анализ, markdown, code fences или заголовки вроде Thinking Process.
Запрещено возвращать массив на верхнем уровне.

JSON должен содержать ровно эти ключи:
- summary_ru: краткое содержание на русском, 3-5 предложений.
- tags: массив из 3-7 релевантных строк; русский язык для тем, английский для терминов.
- author: имя автора строкой или null.
- verification_notes: оценка достоверности высокая/средняя/низкая и короткое обоснование.
- is_tutorial: boolean.
- step_by_step_guidance: строка с шагами или null.

Правила:
- is_tutorial=true только если материал реально учит выполнить задачу по шагам.
- Для науки, новостей, product announcements, обзоров и мнений обычно is_tutorial=false.
- step_by_step_guidance заполняй только когда is_tutorial=true; иначе null.
- Не добавляй символ # в теги.
- Не выдумывай автора.

Заполни и верни объект строго такой формы:
{{
  "summary_ru": "3-5 предложений на русском",
  "tags": ["тег", "term"],
  "author": null,
  "verification_notes": "средняя: короткое обоснование",
  "is_tutorial": false,
  "step_by_step_guidance": null
}}

{source_hint}

Текст:
{extracted_text[:max_chars]}
"""


def create_fallback_metadata(url: str) -> ArticleMetadata:
    return ArticleMetadata(
        summary_ru=f"Ссылка: {url}\n(LLM не справился)",
        tags=["tabs-import"],
        verification_notes="Fallback",
        is_tutorial=False,
        step_by_step_guidance=None,
    )


def normalize_tag(tag: str) -> str:
    """Slugify a tag so identical concepts collapse to one backlink token."""
    cleaned = str(tag).strip()
    cleaned = cleaned.removeprefix("#").strip()
    if cleaned.startswith("[[") and cleaned.endswith("]]"):
        cleaned = cleaned[2:-2].strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned.strip("-")


def normalize_tags(tags: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set = set()
    for tag in tags:
        clean = normalize_tag(tag)
        if clean and clean not in seen:
            normalized.append(clean)
            seen.add(clean)
    return normalized
