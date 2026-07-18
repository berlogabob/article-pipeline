"""Stage 1: URL -> extracted markdown text (+ image URLs) or YouTube transcript."""

import logging
from typing import List, Optional, Tuple, TypedDict

from .errors import ProcessingError, validate_url
from .net import expand_url, get_domain, normalize_url
from .html_parser import extract_image_urls, fetch_and_extract
from .youtube import get_youtube_transcript, is_youtube

logger = logging.getLogger("article_pipeline")


class IngestResult(TypedDict):
    expanded_url: str
    normalized_url: str
    extracted_text: str
    image_urls: List[str]
    is_youtube: bool
    source: str


def ingest_article(
    url: str, title: str, min_length: int = 100
) -> Tuple[Optional[IngestResult], ProcessingError]:
    expanded = expand_url(url) or url
    if not validate_url(expanded):
        logger.info("Invalid URL: %s", expanded[:80])
        return None, ProcessingError.INVALID_URL

    is_yt = is_youtube(expanded)
    extracted: Optional[str] = None
    image_urls: List[str] = []

    try:
        if is_yt:
            extracted = get_youtube_transcript(expanded)
            if not extracted:
                logger.error("No transcript: %s", expanded[:80])
                return None, ProcessingError.EMPTY_CONTENT
        else:
            extracted = fetch_and_extract(expanded)
            if extracted is None:
                logger.error("Extraction failed: %s", expanded[:80])
                return None, ProcessingError.PARSE_ERROR
            image_urls = extract_image_urls(extracted, expanded)
    except Exception as e:
        logger.error("Network error for %s: %s", expanded[:80], e)
        if "timeout" in str(e).lower():
            return None, ProcessingError.TIMEOUT
        return None, ProcessingError.NETWORK_ERROR

    if not extracted or len(extracted) < 10:
        logger.error("Content empty or too short: %s", expanded[:80])
        return None, ProcessingError.EMPTY_CONTENT
    if len(extracted) < min_length:
        logger.warning("Content short (%d chars): %s", len(extracted), expanded[:80])

    return (
        {
            "expanded_url": expanded,
            "normalized_url": normalize_url(expanded),
            "extracted_text": extracted,
            "image_urls": image_urls,
            "is_youtube": is_yt,
            "source": get_domain(expanded),
        },
        ProcessingError.UNKNOWN,
    )
