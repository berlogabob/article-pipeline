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
from typing import List, NotRequired, Optional, TypedDict
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

from .errors import FetchError, ProcessingError
from .net import normalize_url
from .rate_limit import DomainRateLimiter, get_rate_limiter
from .titles import choose_title

logger = logging.getLogger("article_pipeline")

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"})

MAX_CONTENT_SIZE = 10 * 1024 * 1024

# Statuses that will not change on a retry, so the caller can stop asking.
_DEFINITIVE_STATUS = {
    401: ProcessingError.BLOCKED,
    403: ProcessingError.BLOCKED,
    404: ProcessingError.NOT_FOUND,
    410: ProcessingError.NOT_FOUND,
}

_IMAGE_MD_RE = re.compile(
    r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*"|\s+\'[^\']*\')?\s*\)'
)


class ExtractedArticle(TypedDict):
    text: str
    title: Optional[str]
    canonical_url: Optional[str]
    # Set when the body came from Open Graph tags rather than the article
    # itself, so the note can say so and be found again for reprocessing.
    metadata_only: NotRequired[bool]


# An og:description shorter than this carries no information (Kaggle serves
# "Kaggle"), so a note built from it would be worse than the failure.
MIN_METADATA_DESCRIPTION = 40


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


# Model weights, archives, media: fetching one of these pulled a 7 GB
# .safetensors into memory and charmap-decoded it, which froze the machine
# (2026-08-19 reboot). Skip by extension before any download happens.
_BINARY_EXTENSIONS = (
    ".safetensors", ".ckpt", ".gguf", ".pt", ".pth", ".bin", ".onnx",
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".iso", ".dmg",
    ".apk", ".exe", ".msi", ".mp4", ".mov", ".mkv", ".avi", ".mp3",
    ".wav", ".flac",
)


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
    if urlparse(url).path.lower().endswith(_BINARY_EXTENSIONS):
        raise FetchError(
            ProcessingError.INVALID_URL, f"binary file URL skipped: {url[:80]}"
        )

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
            response = _session.get(url, timeout=timeout, stream=True)
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

            # Reaching here with a 429 means the ladder above is spent.
            if response.status_code == 429:
                raise FetchError(
                    ProcessingError.BLOCKED, f"HTTP 429 (rate limited) for {url[:80]}",
                    http_status=429,
                )
            if response.status_code in _DEFINITIVE_STATUS:
                # Say what actually happened. Reported as parse_error these
                # were indistinguishable from an empty page, so a retry pass
                # would keep hammering a site that has refused us outright.
                raise FetchError(
                    _DEFINITIVE_STATUS[response.status_code],
                    f"HTTP {response.status_code} for {url[:80]}",
                    http_status=response.status_code,
                )
            response.raise_for_status()
            ctype = response.headers.get("content-type", "")
            if ctype and not any(
                t in ctype for t in ("text/", "html", "xml", "json")
            ):
                response.close()
                raise FetchError(
                    ProcessingError.INVALID_URL,
                    f"non-text content-type {ctype[:40]} for {url[:80]}",
                )
            # Decode after capping, never before: response.text decodes the
            # whole body first, which is what melted on a 7 GB blob.
            data = b""
            for chunk in response.iter_content(65536):
                data += chunk
                if len(data) > MAX_CONTENT_SIZE:
                    logger.warning("Content truncated (%d bytes)", len(data))
                    break
            response.close()
            content = data[:MAX_CONTENT_SIZE].decode(
                response.encoding or "utf-8", errors="replace"
            )
            logger.info("Fetched via requests: %s", url[:80])
            return content
    except requests.Timeout as e:
        raise FetchError(ProcessingError.TIMEOUT, str(e)) from e
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


def extract_title_from_html(html: str, url: str = "") -> Optional[str]:
    chosen = choose_title(html, url)
    if chosen:
        return chosen
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


# Shell pages that fetch fine but carry no article: JS walls, saved search
# results, CDN challenges, and Habr's "removed by moderator" stub. A note
# built from one is worse than a recorded failure — 40 such notes were
# cleaned out of the vault on 2026-08-02.
_ERROR_PAGE_TITLES = re.compile(
    r"^(javascript is not available|google search|google image result"
    r"|just a moment|access denied|attention required)",
    re.I,
)
_ERROR_PAGE_MARKERS = ("Материал был снят с публикации модератором",)


def _is_error_shell(article: ExtractedArticle) -> bool:
    title = (article.get("title") or "").strip()
    if _ERROR_PAGE_TITLES.match(title):
        return True
    return any(marker in article["text"] for marker in _ERROR_PAGE_MARKERS)


def fetch_and_extract_article(
    url: str,
    timeout: int = 15,
    retry_429_count: int = 3,
    retry_429_backoff_seconds: float = 1.0,
    rate_limiter: Optional[DomainRateLimiter] = None,
    _follow_canonical: bool = True,
) -> Optional[ExtractedArticle]:
    article = _fetch_and_extract_article(
        url,
        timeout=timeout,
        retry_429_count=retry_429_count,
        retry_429_backoff_seconds=retry_429_backoff_seconds,
        rate_limiter=rate_limiter,
        _follow_canonical=_follow_canonical,
    )
    if article and _is_error_shell(article):
        logger.warning(
            "Rejecting error-shell page (title: %r): %s",
            article.get("title"),
            url[:80],
        )
        return None
    return article


def _fetch_and_extract_article(
    url: str,
    timeout: int = 15,
    retry_429_count: int = 3,
    retry_429_backoff_seconds: float = 1.0,
    rate_limiter: Optional[DomainRateLimiter] = None,
    _follow_canonical: bool = True,
) -> Optional[ExtractedArticle]:
    """Fetch `url` and extract text/title/canonical_url in one shot.

    Builds on fetch_html()/extract_text_from_html() the same way
    fetch_and_extract() does, but also resolves the page's resolved title and
    canonical URL, and falls back to og:/twitter: metadata extraction for a
    handful of sites trafilatura can't parse (see _metadata_fallback_article),
    and, when nothing is extractable, one retry against the page's declared
    canonical URL (see _extract_from_canonical). Reddit posts are scraped from
    old.reddit.com before any of that (fetch_reddit_post_article), and the
    r.jina.ai reader is the last resort for blocked or JS-only pages
    (fetch_reader_article) — both ported from the old logseq-processor.
    """
    try:
        reddit_article = fetch_reddit_post_article(url, timeout=timeout)
    except FetchError:
        reader_article = fetch_reader_article(url, timeout=timeout)
        if reader_article:
            return reader_article
        raise
    if reddit_article:
        return reddit_article

    try:
        html = fetch_html(
            url,
            timeout=timeout,
            retry_429_count=retry_429_count,
            retry_429_backoff_seconds=retry_429_backoff_seconds,
            rate_limiter=rate_limiter,
        )
    except FetchError:
        # The site refused us outright (403/429/...). A reader proxy fetches
        # from its own IPs, so it often succeeds where we are blocked.
        reader_article = fetch_reader_article(url, timeout=timeout)
        if reader_article:
            return reader_article
        raise
    if not html:
        return fetch_reader_article(url, timeout=timeout)
    if html.startswith("%PDF"):
        # An extensionless PDF link (arxiv.org/pdf/...): not our job — stage1
        # sniffs the magic bytes and extracts locally with pypdf.
        return None

    text = extract_text_from_html(html, include_images=True)
    title = extract_title_from_html(html, url)
    canonical_url = extract_canonical_url_from_html(html)
    if text is None:
        # Social hosts serve a JS shell whose og:description is only the
        # first post. The reader proxy renders the full thread, so it goes
        # first; the metadata stub remains the fallback, not the terminus.
        if _is_social_host(url):
            reader_article = fetch_reader_article(url, timeout=timeout)
            if reader_article:
                return reader_article
        fallback_article = _metadata_fallback_article(
            url,
            html,
            title=title,
            canonical_url=canonical_url,
        )
        if fallback_article:
            return fallback_article
        # Nothing extractable here, but the page told us where it really
        # lives. Redirectors and share interstitials (share.google, link
        # wrappers) render no article text of their own while still carrying
        # og:url/rel=canonical for the target — following it once turns the
        # single largest failure class into a normal fetch.
        if _follow_canonical and canonical_url:
            canonical_article = _extract_from_canonical(
                url,
                canonical_url,
                timeout=timeout,
                retry_429_count=retry_429_count,
                retry_429_backoff_seconds=retry_429_backoff_seconds,
                rate_limiter=rate_limiter,
            )
            if canonical_article:
                return canonical_article
        # Page fetched but nothing extractable anywhere — the body is behind
        # JavaScript (Kaggle et al.). Last resort: the reader proxy renders it.
        return fetch_reader_article(url, timeout=timeout)
    return {
        "text": text,
        "title": title,
        "canonical_url": canonical_url,
    }


def _extract_from_canonical(
    url: str,
    canonical_url: str,
    **kwargs,
) -> Optional[ExtractedArticle]:
    """Retry extraction against a page's declared canonical URL, exactly once.

    Guarded on the *normalized* URLs differing, so a page whose canonical is
    itself — the common case — costs nothing and cannot loop. The retry passes
    _follow_canonical=False, so a chain of redirectors stops after one hop
    rather than walking indefinitely.
    """
    if normalize_url(canonical_url) == normalize_url(url):
        return None
    logger.info("Retrying via canonical URL: %s -> %s", url[:60], canonical_url[:60])
    article = fetch_and_extract_article(
        canonical_url, _follow_canonical=False, **kwargs
    )
    if article is None:
        return None
    # Report the canonical we followed, not whatever the target re-declares,
    # so the note is filed under the URL we actually read.
    return {**article, "canonical_url": canonical_url}


def _metadata_fallback_article(
    url: str,
    html: str,
    *,
    title: Optional[str],
    canonical_url: Optional[str],
) -> Optional[ExtractedArticle]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    soup = BeautifulSoup(html, "html.parser")
    if host == "huggingface.co":
        return _huggingface_metadata_article(url, soup, title, canonical_url)
    if host != "instagram.com":
        return _generic_metadata_article(url, soup, title, canonical_url)

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


def _generic_metadata_article(
    url: str,
    soup: BeautifulSoup,
    title: Optional[str],
    canonical_url: Optional[str],
) -> Optional[ExtractedArticle]:
    """Last resort for pages whose article body lives only in JavaScript.

    Social posts and SPA shells — X, Threads, YouTube, most modern app pages —
    serve a bare skeleton plus Open Graph tags. For a short-form post the
    og:description *is* the content; for a long article it is a summary.
    Either beats dropping the link, provided the note admits the body is
    metadata only, which is what `metadata_only` marks for the renderer.

    The description floor exists because a one-word og:description ("Kaggle")
    produces a note with no information in it at all.
    """
    description = (
        _meta_content(soup, "og:description")
        or _meta_content(soup, "twitter:description")
        or _meta_content(soup, "description")
    )
    if not description or len(description.strip()) < MIN_METADATA_DESCRIPTION:
        return None
    resolved_title = (
        _meta_content(soup, "og:title") or _meta_content(soup, "twitter:title") or title
    )
    if not resolved_title:
        return None

    canonical = canonical_url or _meta_content(soup, "og:url") or url
    site = _meta_content(soup, "og:site_name") or urlparse(
        canonical
    ).netloc.lower().removeprefix("www.")
    logger.info("Using metadata fallback for %s: %s", site, url[:80])
    return {
        "text": "\n".join(
            [f"Source: {site}", f"URL: {canonical}", "", description.strip()]
        ),
        "title": _clean_title(resolved_title),
        "canonical_url": canonical,
        "metadata_only": True,
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


def fetch_reddit_post_article(url: str, timeout: int = 15) -> Optional[ExtractedArticle]:
    """Fetch Reddit JSON, falling back to old.reddit.com plain HTML.

    www.reddit.com is a JS shell trafilatura extracts nothing from. Share
    links (/r/sub/s/xxxx) are resolved to their /comments/ URL first.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in {"reddit.com", "old.reddit.com"}:
        return None
    if "/comments/" not in parsed.path:
        if "/s/" not in parsed.path:
            return None
        try:
            response = _session.get(url, timeout=timeout, allow_redirects=True)
            parsed = urlparse(response.url)
        except Exception as e:
            logger.debug("Reddit share-link resolve failed for %s: %s", url[:80], e)
            return None
        if "/comments/" not in parsed.path:
            return None

    json_url = f"https://www.reddit.com{parsed.path.rstrip('/')}.json?raw_json=1&limit=50"
    try:
        response = _session.get(
            json_url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        if response.status_code in (403, 429):
            raise FetchError(
                ProcessingError.BLOCKED,
                f"HTTP {response.status_code} (reddit blocked) for {url[:80]}",
                http_status=response.status_code,
            )
        response.raise_for_status()
        listing = response.json()
        post = listing[0]["data"]["children"][0]["data"]
        title = _clean_title(post["title"])
        permalink = post["permalink"]
        if not title or not isinstance(permalink, str) or not permalink.startswith("/"):
            raise ValueError("Invalid Reddit post")
        canonical = f"https://www.reddit.com{permalink}"
        lines = ["Source: Reddit", f"URL: {canonical}"]
        if post.get("author"):
            lines.append(f"Author: u/{post['author']}")
        if post.get("subreddit"):
            lines.append(f"Subreddit: r/{post['subreddit']}")
        if post.get("score") is not None:
            lines.append(f"Score: {post['score']}")
        lines.extend(["", "Title:", title])
        if post.get("selftext"):
            lines.extend(["", "Post:", post["selftext"].strip()])
        if not post.get("is_self", bool(post.get("selftext"))) and post.get("url"):
            lines.extend(["", f"Link: {post['url']}"])

        comments = []
        remaining = 12000
        for child in listing[1]["data"]["children"]:
            if child.get("kind") != "t1":
                continue
            comment = child["data"]
            author, body = comment.get("author"), comment.get("body")
            if not isinstance(author, str) or not isinstance(body, str):
                continue
            body = body.strip()
            if (author.lower() in {"[deleted]", "[removed]"}
                    or body.lower() in {"", "[deleted]", "[removed]"}
                    or (author.lower() == "automoderator" and comment.get("stickied"))):
                continue
            prefix = f"u/{author}: "
            if remaining <= len(prefix):
                break
            available = remaining - len(prefix)
            if len(body) > available:
                body = body[:available - 1].rstrip() + "…"
            entry = prefix + body
            comments.append(entry)
            remaining -= len(entry) + 2
            if len(comments) == 15 or remaining <= 0:
                break
        if comments:
            lines.extend(["", "Top comments:", "\n\n".join(comments)])
        return {"text": "\n".join(lines), "title": title, "canonical_url": canonical}
    except FetchError:
        raise
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError):
        pass

    old_url = f"https://old.reddit.com{parsed.path.rstrip('/')}/"
    try:
        response = _session.get(old_url, timeout=timeout)
        response.raise_for_status()
    except Exception as e:
        logger.debug("old.reddit fetch failed for %s: %s", url[:80], e)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    thing = soup.find("div", {"data-fullname": lambda v: v and v.startswith("t3_")})
    if not thing:
        thing = soup.find("div", class_="thing")
    if not thing:
        return None

    title_tag = thing.find("a", class_="title")
    title = _clean_title(title_tag.get_text(" ", strip=True)) if title_tag else ""
    author_tag = thing.find("a", class_="author")
    author = author_tag.get_text(strip=True) if author_tag else ""
    body_tag = thing.find("div", class_="md")
    body = body_tag.get_text("\n", strip=True) if body_tag else ""
    if not title and not body:
        return None

    canonical = f"https://www.reddit.com{parsed.path.rstrip('/')}/"
    lines = ["Source: Reddit", f"URL: {canonical}"]
    if author:
        lines.append(f"Author: u/{author}")
    if title:
        lines.extend(["", "Title:", title])
    if body:
        lines.extend(["", "Post:", html_lib.unescape(body)])
    text = "\n".join(lines).strip()
    logger.info("Using old.reddit fallback: %s", url[:80])
    return {
        "text": text,
        "title": title or "Reddit post",
        "canonical_url": canonical,
    }


_SOCIAL_HOSTS = {"x.com", "twitter.com", "threads.com", "threads.net", "instagram.com"}


def _is_social_host(url: str) -> bool:
    return urlparse(url).netloc.lower().removeprefix("www.") in _SOCIAL_HOSTS


def _resolve_final_url(url: str, timeout: int = 15) -> str:
    """Follow redirects to the URL's final location, ignoring the status.

    Share wrappers (share.google et al.) redirect to the real article; even
    when the target 403s us, the response still carries the final URL. Notes
    get filed under the real source domain instead of the wrapper's.
    """
    try:
        # stream=True: only headers are needed for the final URL; without it
        # this GET downloads the full body (once a 7 GB .safetensors).
        response = _session.get(
            url, timeout=timeout, allow_redirects=True, stream=True
        )
        final = str(response.url)
        response.close()
        if validate_url(final):
            return final
    except Exception as e:
        logger.debug("Redirect resolve failed for %s: %s", url[:80], e)
    return url


def fetch_reader_article(url: str, timeout: int = 15) -> Optional[ExtractedArticle]:
    """Fetch through the r.jina.ai reader proxy, which renders JS and is not
    blocked where we are. Last-resort fallback: returns markdown or None."""
    url = _resolve_final_url(url, timeout=timeout)
    if urlparse(url).path.lower().endswith(_BINARY_EXTENSIONS):
        logger.debug("Reader fallback skipped, binary URL: %s", url[:80])
        return None
    reader_url = f"https://r.jina.ai/http://{url}"
    try:
        response = _session.get(reader_url, timeout=max(timeout, 30))
        response.raise_for_status()
    except Exception as e:
        logger.debug("Reader fallback failed for %s: %s", url[:80], e)
        return None

    raw = response.text.strip()
    title = _reader_field(raw, "Title")
    source_url = _reader_field(raw, "URL Source")
    content = (
        raw.split("Markdown Content:", 1)[1].strip()
        if "Markdown Content:" in raw
        else raw
    )
    if "Warning: Target URL returned error" in raw and len(content) < 500:
        return None
    if title.lower().startswith("just a moment"):
        return None
    if len(content) < 200:
        return None

    canonical = source_url if validate_url(source_url) else url
    text = "\n".join(
        [
            "Source: Reader fallback",
            f"URL: {canonical}",
            "",
            content,
        ]
    ).strip()
    logger.info("Using reader fallback: %s", url[:80])
    return {
        "text": text,
        "title": _clean_title(title or urlparse(url).netloc),
        "canonical_url": canonical,
    }


def _reader_field(raw: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", raw, flags=re.M)
    return match.group(1).strip() if match else ""


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
