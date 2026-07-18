"""Fetch and extract article text from HTML.

Ported from the old logseq-processor project's src/html_parser.py, with the
Config-singleton dependency removed: retry/backoff/min-length tunables are
now explicit parameters (defaults match the old config values) and the rate
limiter is injectable instead of pulled from a global.

New: extract_image_urls() pulls image references out of the markdown that
trafilatura.extract() now emits (include_images=True), so callers can
download the article's images separately (see images.py).
"""

import html as html_lib
import logging
import re
import time
from typing import List, Optional, TypedDict
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

from .rate_limit import DomainRateLimiter, get_rate_limiter

logger = logging.getLogger("article_pipeline")

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"})

MAX_CONTENT_SIZE = 10 * 1024 * 1024

_IMAGE_MD_RE = re.compile(
    r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*"|\s+\'[^\']*\')?\s*\)'
)


class ExtractedArticle(TypedDict):
    text: str
    title: Optional[str]
    canonical_url: Optional[str]


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


def extract_title_from_html(html: str) -> Optional[str]:
    try:
        metadata = trafilatura.extract_metadata(html)
        title = getattr(metadata, "title", None) if metadata else None
        if title:
            return _clean_title(title)
    except Exception as e:
        logger.debug("Trafilatura metadata title extraction failed: %s", e)

    try:
        soup = BeautifulSoup(html, "html.parser")
        for selector in (
            ("meta", {"property": "og:title"}),
            ("meta", {"name": "twitter:title"}),
        ):
            tag = soup.find(*selector)
            if tag and tag.get("content"):
                return _clean_title(tag["content"])
        if soup.title and soup.title.string:
            return _clean_title(soup.title.string)
    except Exception as e:
        logger.debug("HTML title extraction failed: %s", e)
    return None


def extract_canonical_url_from_html(html: str) -> Optional[str]:
    try:
        metadata = trafilatura.extract_metadata(html)
        metadata_url = getattr(metadata, "url", None) if metadata else None
        if metadata_url and validate_url(metadata_url):
            return metadata_url.strip()
    except Exception as e:
        logger.debug("Trafilatura metadata URL extraction failed: %s", e)

    try:
        soup = BeautifulSoup(html, "html.parser")
        for selector in (
            ("meta", {"property": "og:url"}),
            ("meta", {"name": "twitter:url"}),
        ):
            tag = soup.find(*selector)
            if tag and tag.get("content") and validate_url(tag["content"]):
                return tag["content"].strip()

        canonical = soup.find(
            "link", rel=lambda value: value and "canonical" in value
        )
        if canonical and canonical.get("href") and validate_url(canonical["href"]):
            return canonical["href"].strip()
    except Exception as e:
        logger.debug("HTML canonical URL extraction failed: %s", e)
    return None


def _clean_title(title: str) -> str:
    return " ".join(title.split()).strip()


def fetch_and_extract_article(
    url: str,
    timeout: int = 15,
    retry_429_count: int = 3,
    retry_429_backoff_seconds: float = 1.0,
    rate_limiter: Optional[DomainRateLimiter] = None,
) -> Optional[ExtractedArticle]:
    """Fetch `url` and extract text/title/canonical_url in one shot.

    Builds on fetch_html()/extract_text_from_html() the same way
    fetch_and_extract() does, but also resolves the page's resolved title and
    canonical URL, and falls back to og:/twitter: metadata extraction for a
    handful of sites trafilatura can't parse (see _metadata_fallback_article).
    """
    html = fetch_html(
        url,
        timeout=timeout,
        retry_429_count=retry_429_count,
        retry_429_backoff_seconds=retry_429_backoff_seconds,
        rate_limiter=rate_limiter,
    )
    if not html:
        return None

    text = extract_text_from_html(html, include_images=True)
    title = extract_title_from_html(html)
    canonical_url = extract_canonical_url_from_html(html)
    if text is None:
        fallback_article = _metadata_fallback_article(
            url,
            html,
            title=title,
            canonical_url=canonical_url,
        )
        if fallback_article:
            return fallback_article
        return None
    return {
        "text": text,
        "title": title,
        "canonical_url": canonical_url,
    }


def _metadata_fallback_article(
    url: str,
    html: str,
    *,
    title: Optional[str],
    canonical_url: Optional[str],
) -> Optional[ExtractedArticle]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in {"instagram.com", "huggingface.co"}:
        return None

    soup = BeautifulSoup(html, "html.parser")
    if host == "huggingface.co":
        return _huggingface_metadata_article(url, soup, title, canonical_url)

    og_title = _meta_content(soup, "og:title")
    og_description = _meta_content(soup, "og:description")
    twitter_title = _meta_content(soup, "twitter:title")
    og_image = _meta_content(soup, "og:image")
    canonical = canonical_url or _meta_content(soup, "og:url") or url

    caption = _instagram_caption(og_title) or _instagram_caption(og_description)
    author = _instagram_author(og_title, twitter_title)
    stats = _instagram_stats(og_description)

    lines = ["Source: Instagram", f"URL: {canonical}"]
    if author:
        lines.append(f"Author: {author}")
    if caption:
        lines.extend(["", "Caption:", caption])
    elif og_description:
        lines.extend(["", "Description:", og_description.strip()])
    if stats:
        lines.append(f"Stats: {stats}")
    if og_image:
        lines.append(f"Image: {og_image}")

    text = "\n".join(line for line in lines if line is not None).strip()
    if len(text) < 10:
        return None

    fallback_title = twitter_title or title or og_title or "Instagram post"
    logger.info("Using metadata fallback for Instagram: %s", url[:80])
    return {
        "text": text,
        "title": _clean_title(fallback_title),
        "canonical_url": canonical,
    }


def _huggingface_metadata_article(
    url: str,
    soup: BeautifulSoup,
    title: Optional[str],
    canonical_url: Optional[str],
) -> Optional[ExtractedArticle]:
    og_title = _meta_content(soup, "og:title")
    og_description = _meta_content(soup, "og:description")
    description = _meta_content(soup, "description")
    canonical = canonical_url or _meta_content(soup, "og:url") or url
    fallback_title = title or og_title or "Hugging Face"
    body = og_description or description
    if not body:
        return None
    text = "\n".join(
        [
            "Source: Hugging Face",
            f"URL: {canonical}",
            "",
            "Description:",
            body.strip(),
        ]
    )
    logger.info("Using metadata fallback for Hugging Face: %s", url[:80])
    return {
        "text": text,
        "title": _clean_title(fallback_title),
        "canonical_url": canonical,
    }


def _meta_content(soup: BeautifulSoup, key: str) -> Optional[str]:
    tag = soup.find("meta", {"property": key}) or soup.find("meta", {"name": key})
    if tag and tag.get("content"):
        return html_lib.unescape(str(tag["content"])).strip()
    return None


def _instagram_caption(*values: Optional[str]) -> str:
    for value in values:
        if not value:
            continue
        match = re.search(r':\s*"(?P<caption>.*)"\s*\.?\s*$', value, flags=re.S)
        if match:
            return match.group("caption").strip()
    return ""


def _instagram_author(og_title: Optional[str], twitter_title: Optional[str]) -> str:
    if og_title:
        match = re.match(r"(?P<author>.+?)\s+on Instagram:", og_title, flags=re.S)
        if match:
            return _clean_title(match.group("author"))
    if twitter_title:
        match = re.match(r"(?P<author>.+?)\s+\(@(?P<handle>[^)]+)\)", twitter_title)
        if match:
            return f"{_clean_title(match.group('author'))} (@{match.group('handle')})"
    return ""


def _instagram_stats(description: Optional[str]) -> str:
    if not description:
        return ""
    match = re.match(
        r"(?P<stats>[\d,.\s]+likes?,\s*[\d,.\s]+comments?)\s+-\s+",
        description,
        flags=re.I,
    )
    return _clean_title(match.group("stats")) if match else ""
