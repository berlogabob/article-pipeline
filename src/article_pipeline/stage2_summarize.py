"""Stage 2: extracted text -> ArticleMetadata via the configured engine.

Retry ladder: primary model over each temperature, then fallback model,
then create_fallback_metadata as the last resort.
"""

import logging
from typing import List, Optional

from pydantic import ValidationError

from .metadata import (
    ArticleMetadata,
    build_prompt,
    create_fallback_metadata,
    normalize_tag,
)
from .net import clean_json

logger = logging.getLogger("article_pipeline")


def summarize(
    engine,
    extracted_text: str,
    primary_model: str,
    fallback_model: Optional[str] = None,
    temperature_attempts: Optional[List[float]] = None,
    is_youtube: bool = False,
    max_prompt_chars: int = 8000,
    url: str = "",
) -> ArticleMetadata:
    temps = temperature_attempts or [0.05, 0.25, 0.50]
    prompt = build_prompt(extracted_text, is_youtube=is_youtube, max_chars=max_prompt_chars)

    models = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models.append(fallback_model)

    for model in models:
        for temp in temps:
            try:
                logger.info("LLM call: model=%s temp=%.2f", model or "(default)", temp)
                raw = engine.chat_json(prompt, model, temp)
                meta = ArticleMetadata.model_validate_json(clean_json(raw))
                meta.tags = [normalize_tag(t) for t in meta.tags if t.strip()]
                return meta
            except ValidationError:
                logger.warning("Invalid JSON from %s (temp=%.2f), retrying", model, temp)
            except TimeoutError:
                logger.warning("Timeout from %s (temp=%.2f); trying next model", model, temp)
                break  # don't stack more slow calls on the same model
            except Exception as e:
                logger.warning("LLM error from %s: %s: %s", model, type(e).__name__, e)

    logger.error("All LLM attempts failed, using fallback metadata")
    return create_fallback_metadata(url)
