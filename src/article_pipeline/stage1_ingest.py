"""Stage 1: URL -> extracted markdown text (+ image URLs) or YouTube transcript."""

import logging
from typing import List, NotRequired, Optional, Tuple, TypedDict

from .errors import ProcessingError, validate_url
from .net import expand_url, get_domain, normalize_url
from .html_parser import extract_image_urls, fetch_and_extract_article
from .youtube import (
    YouTubePlaylistVideo,
    get_youtube_playlist_metadata,
    get_youtube_transcript,
    is_youtube,
    is_youtube_homepage,
    is_youtube_playlist,
)

logger = logging.getLogger("article_pipeline")


class IngestResult(TypedDict):
    expanded_url: str
    normalized_url: str
    resolved_title: str
    extracted_text: str
    image_urls: List[str]
    is_youtube: bool
    source: str
    canonical_url: str
    playlist_videos: NotRequired[list[YouTubePlaylistVideo]]


def ingest_article(
    url: str,
    title: str,
    min_length: int = 100,
    force_youtube_asr: bool = False,
) -> Tuple[Optional[IngestResult], ProcessingError]:
    expanded = expand_url(url) or url
    if not validate_url(expanded):
        logger.info("Invalid URL: %s", expanded[:80])
        return None, ProcessingError.INVALID_URL

    final_url = expanded

    if is_youtube_homepage(final_url):
        logger.warning("Skipping YouTube homepage URL: %s", final_url[:80])
        return None, ProcessingError.INVALID_URL

    is_playlist = is_youtube_playlist(final_url)
    is_yt = is_youtube(final_url)
    extracted: Optional[str] = None
    image_urls: List[str] = []
    resolved_title = ""
    canonical_url = ""
    playlist_videos: List[YouTubePlaylistVideo] = []

    try:
        if is_playlist:
            playlist_metadata = get_youtube_playlist_metadata(final_url)
            if not playlist_metadata:
                return None, ProcessingError.EMPTY_CONTENT
            extracted = playlist_metadata["text"]
            resolved_title = playlist_metadata["title"] or ""
            playlist_canonical = playlist_metadata["canonical_url"] or ""
            if playlist_canonical:
                canonical_url = playlist_canonical
                final_url = playlist_canonical
            is_yt = True
            playlist_videos = playlist_metadata.get("videos", [])
        elif is_yt:
            extracted = get_youtube_transcript(final_url, force_asr=force_youtube_asr)
            if not extracted:
                logger.error(
                    "YouTube transcript unavailable after all transcript layers: %s",
                    final_url[:80],
                )
                return None, ProcessingError.EMPTY_CONTENT
        else:
            fetched_url = final_url
            article = fetch_and_extract_article(fetched_url)
            if article is None:
                logger.error("Extraction failed: %s", fetched_url[:80])
                return None, ProcessingError.PARSE_ERROR
            extracted = article["text"]
            image_urls = extract_image_urls(extracted, fetched_url)
            if article.get("title"):
                resolved_title = article["title"] or ""
            article_canonical_url = article.get("canonical_url")
            if article_canonical_url and validate_url(article_canonical_url):
                canonical_url = article_canonical_url
                final_url = canonical_url
                is_yt = is_youtube(final_url)
    except Exception as e:
        logger.error("Network error for %s: %s", final_url[:80], e)
        if "timeout" in str(e).lower():
            return None, ProcessingError.TIMEOUT
        return None, ProcessingError.NETWORK_ERROR

    if not extracted or len(extracted) < 10:
        logger.error("Content empty or too short: %s", final_url[:80])
        return None, ProcessingError.EMPTY_CONTENT
    if len(extracted) < min_length:
        logger.warning("Content short (%d chars): %s", len(extracted), final_url[:80])

    result: IngestResult = {
        "expanded_url": final_url,
        "normalized_url": normalize_url(final_url),
        "resolved_title": resolved_title,
        "extracted_text": extracted,
        "image_urls": image_urls,
        "is_youtube": is_yt,
        "source": get_domain(final_url),
        "canonical_url": canonical_url,
    }
    if playlist_videos:
        result["playlist_videos"] = playlist_videos

    return result, ProcessingError.UNKNOWN
