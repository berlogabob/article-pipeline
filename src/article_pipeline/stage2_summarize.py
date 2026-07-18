"""Stage 2: extracted text -> ArticleMetadata via the configured engine.

Retry ladder: primary model over each temperature, then fallback model,
then create_fallback_metadata as the last resort.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .metadata import (
    METADATA_JSON_KEYS,
    ArticleMetadata,
    build_prompt,
    create_fallback_metadata,
    normalize_tags,
)
from .net import clean_json

logger = logging.getLogger("article_pipeline")


def extract_json_object(content: str) -> Dict[str, Any]:
    """Pull the metadata JSON object out of possibly-chatty LLM output.

    Scans for every parseable {...} via raw_decode and prefers the last one
    containing all metadata keys (ported from the oMLX variant's omlx_client).
    """
    decoder = json.JSONDecoder()
    found: List[Dict[str, Any]] = []
    for idx, char in enumerate(content):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(content[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            found.append(parsed)

    for parsed in reversed(found):
        if METADATA_JSON_KEYS.issubset(parsed.keys()):
            return parsed
    if found:
        return found[-1]

    json_text = clean_json(content)
    if json_text == "{}" and content.strip() != "{}":
        raise ValueError("LLM output did not contain a complete JSON object")
    parsed = json.loads(json_text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON response must be an object")
    return parsed


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
                meta = ArticleMetadata.model_validate(extract_json_object(raw))
                meta.tags = normalize_tags(meta.tags)
                return meta
            except (ValidationError, ValueError, json.JSONDecodeError):
                logger.warning("Invalid JSON from %s (temp=%.2f), retrying", model, temp)
            except TimeoutError:
                logger.warning("Timeout from %s (temp=%.2f); trying next model", model, temp)
                break  # don't stack more slow calls on the same model
            except Exception as e:
                logger.warning("LLM error from %s: %s: %s", model, type(e).__name__, e)

    logger.error("All LLM attempts failed, using fallback metadata")
    return create_fallback_metadata(url)
