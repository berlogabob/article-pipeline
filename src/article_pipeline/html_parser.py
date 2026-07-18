"""Fetch and extract article text from HTML.

Ported from the old logseq-processor project's src/html_parser.py, with the
Config-singleton dependency removed: retry/backoff/min-length tunables are
now explicit parameters (defaults match the old config values) and the rate
limiter is injectable instead of pulled from a global.

New: extract_image_urls() pulls image references out of the markdown that
trafilatura.extract() now emits (include_images=True), so callers can
download the article's images separately (see images.py).
"""

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
import trafilatura

from .rate_limit import DomainRateLimiter, get_rate_limiter

logger = logging.getLogger("article_pipeline")

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"})

MAX_CONTENT_SIZE = 10 * 1024 * 1024

_IMAGE_MD_RE = re.compile(
    r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*"|\s+\'[^\']*\')?\s*\)'
)


def validate_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    if len(url) > 2048:
        return False
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except (ValueError, AttributeError):
        return False


def fetch_html(
    url: str,
    timeout: int = 15,
    retry_429_count: int = 3,
    retry_429_backoff_seconds: float = 1.0,
    rate_limiter: Optional[DomainRateLimiter] = None,
) -> Optional[str]:
    if not validate_url(url):
        logger.error("Invalid URL provided: %s", url[:100])
        return None

    limiter = rate_limiter if rate_limiter is not None else get_rate_limiter()
    limiter.wait(url)

    try:
        html = trafilatura.fetch_url(url)
        if html:
            logger.info("Fetched via trafilatura: %s", url[:80])
            return html
        logger.warning("Trafilatura empty: %s", url[:80])
    except Exception as e:
        logger.warning("Trafilatura failed: %s. Using requests...", e)

    try:
        retry_count = max(0, int(retry_429_count))
        base_backoff = max(0.0, float(retry_429_backoff_seconds))
        max_attempts = retry_count + 1

        for attempt in range(max_attempts):
            response = _session.get(url, timeout=timeout)
            if response.status_code == 429 and attempt < max_attempts - 1:
                backoff = base_backoff * (2**attempt)
                logger.warning(
                    "HTTP 429 for %s (attempt %d/%d), retrying in %.2fs",
                    url[:80],
                    attempt + 1,
                    max_attempts,
                    backoff,
                )
                response.close()
                if backoff > 0:
                    time.sleep(backoff)
                continue

            response.raise_for_status()
            content = response.text
            if len(content) > MAX_CONTENT_SIZE:
                logger.warning("Content truncated (%d bytes)", len(content))
                content = content[:MAX_CONTENT_SIZE]
            logger.info("Fetched via requests: %s", url[:80])
            return content
    except requests.RequestException as e:
        logger.error("Requests failed: %s", e)
        return None


def extract_text_from_html(html: str, include_images: bool = True) -> Optional[str]:
    try:
        result = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=include_images,
            favor_precision=True,
        )
        if result:
            logger.info("Extracted %d chars from HTML", len(result))
        else:
            logger.warning("No content extracted from HTML")
        return result
    except Exception as e:
        logger.error("Failed to extract text from HTML: %s", e)
        return None


def extract_image_urls(markdown_text: str, base_url: str) -> List[str]:
    """Pull image URLs out of markdown `![alt](url)` references.

    Relative URLs are made absolute against `base_url`; duplicates are
    dropped while preserving first-seen order.
    """
    if not markdown_text:
        return []

    seen = set()
    urls: List[str] = []
    for match in _IMAGE_MD_RE.finditer(markdown_text):
        raw = match.group(1).strip()
        if not raw:
            continue
        absolute = urljoin(base_url, raw)
        if absolute not in seen:
            seen.add(absolute)
            urls.append(absolute)
    return urls


def fetch_and_extract(
    url: str,
    timeout: int = 15,
    retry_429_count: int = 3,
    retry_429_backoff_seconds: float = 1.0,
    min_length: int = 100,
    include_images: bool = True,
    rate_limiter: Optional[DomainRateLimiter] = None,
) -> Optional[str]:
    html = fetch_html(
        url,
        timeout=timeout,
        retry_429_count=retry_429_count,
        retry_429_backoff_seconds=retry_429_backoff_seconds,
        rate_limiter=rate_limiter,
    )
    if not html:
        return None

    extracted = extract_text_from_html(html, include_images=include_images)
    if extracted and len(extracted) < min_length:
        logger.warning(
            "Content short (%d chars, min: %d): %s", len(extracted), min_length, url[:80]
        )
        return None
    return extracted
