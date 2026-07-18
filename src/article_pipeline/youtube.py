"""YouTube transcript fetching.

Ported from the old logseq-processor project's src/youtube.py, with the
Config-singleton dependency removed: the rate limiter is injectable instead
of pulled from a global.
"""

import logging
import re
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi

from .rate_limit import DomainRateLimiter, get_rate_limiter

logger = logging.getLogger("article_pipeline")

YOUTUBE_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
]


def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def get_youtube_transcript(
    url: str,
    timeout: int = 15,
    rate_limiter: Optional[DomainRateLimiter] = None,
) -> Optional[str]:
    try:
        video_id = _extract_video_id(url)
        logger.info("Fetching transcript for YouTube video: %s", video_id)

        limiter = rate_limiter if rate_limiter is not None else get_rate_limiter()
        limiter.wait(url)

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        for lang in ["ru", "en"]:
            try:
                transcript = transcript_list.find_transcript([lang])
                text = "\n".join(item["text"] for item in transcript.fetch())
                logger.info(
                    "Found %s transcript for %s: %d chars", lang, video_id, len(text)
                )
                return text
            except Exception as e:
                logger.debug("No %s transcript found: %s", lang, e)
                continue

        try:
            transcript = transcript_list.find_generated_transcript()
            text = "\n".join(item["text"] for item in transcript.fetch())
            logger.info(
                "Using auto-generated transcript for %s: %d chars", video_id, len(text)
            )
            return text
        except Exception as e:
            logger.warning(
                "No auto-generated transcript available for %s: %s", video_id, e
            )
            return None
    except Exception as e:
        logger.error("Failed to get transcript for %s: %s", url[:80], e)
        return None


def _extract_video_id(url: str) -> str:
    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    raise ValueError(f"Could not extract video ID from URL: {url}")
