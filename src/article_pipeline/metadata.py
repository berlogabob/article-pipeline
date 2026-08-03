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


def build_vocabulary_section(vocabulary: Optional[List[str]]) -> str:
    """Ask the model to reuse the library's existing tags.

    Without this every article invents its own tags: of 4345 tags in the vault,
    3617 were used exactly once, so half the articles had no tag shared with
    5+ notes and could not be clustered at all. A tag nobody else uses is a
    tag that groups nothing.
    """
    if not vocabulary:
        return ""
    return (
        "\nСловарь тегов библиотеки:\n"
        + ", ".join(vocabulary)
        + "\nВыбирай теги ТОЛЬКО из этого словаря. Разрешается не более ОДНОГО "
        "нового тега, и только если ни один тег словаря не описывает тему. "
        "Лишние новые теги будут отброшены.\n"
    )


def build_prompt(
    extracted_text: str,
    is_youtube: bool = False,
    max_chars: int = 8000,
    vocabulary: Optional[List[str]] = None,
) -> str:
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
{build_vocabulary_section(vocabulary)}
Текст:
{extracted_text[:max_chars]}
"""


def normalize_tag(tag: str) -> str:
    """Slugify a tag so identical concepts collapse to one backlink token.

    Lowercased to match _normalizeTag in TyLog's scanner.dart: the app compares
    tags by exact string, so `AI` and `ai` would otherwise be two concepts that
    each fall short of the clustering threshold.
    """
    cleaned = str(tag).strip()
    cleaned = cleaned.removeprefix("#").strip()
    if cleaned.startswith("[[") and cleaned.endswith("]]"):
        cleaned = cleaned[2:-2].strip()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    return cleaned.strip("-").lower()


def constrain_tags(tags: List[str], vocabulary: Optional[List[str]]) -> List[str]:
    """Keep vocabulary tags plus at most one new tag.

    Prompt-level advice measurably failed (78% of vault tags were
    singletons), so the vocabulary is enforced here: minted tags beyond the
    first are dropped. Comparison is on normalized forms, matching how the
    app clusters. With no vocabulary (young vault), tags pass through.
    """
    if not vocabulary:
        return tags
    vocab = {normalize_tag(v) for v in vocabulary}
    kept: List[str] = []
    minted = 0
    for tag in tags:
        if normalize_tag(tag) in vocab:
            kept.append(tag)
        elif minted == 0:
            kept.append(tag)
            minted = 1
    return kept or tags[:3]


def normalize_tags(tags: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set = set()
    for tag in tags:
        clean = normalize_tag(tag)
        if clean and clean not in seen:
            normalized.append(clean)
            seen.add(clean)
    return normalized
