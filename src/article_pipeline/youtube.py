"""YouTube transcript/metadata fetching.

Ported from the restored logseq-processor project's src/youtube.py. The only
deliberate deviation from the source is the Config-singleton removal: the
rate limiter comes from `.rate_limit.get_rate_limiter()` (already
dependency-free in this project) and logging uses the module-level
`article_pipeline` logger instead of `.common`. Env var names, function
names/signatures, and the API -> yt-dlp -> ASR transcript fallback chain are
kept identical to the source.
"""

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, TypedDict
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from .rate_limit import get_rate_limiter

logger = logging.getLogger("article_pipeline")

VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


class YouTubeVideoMetadata(TypedDict):
    text: str
    title: str
    canonical_url: str


class YouTubePlaylistVideo(TypedDict):
    index: int
    title: str
    url: str
    video_id: str
    published: str
    description: str
    transcript_excerpt: str


class YouTubePlaylistMetadata(TypedDict):
    text: str
    title: str
    canonical_url: str
    videos: list[YouTubePlaylistVideo]


def is_youtube(url: str) -> bool:
    return extract_video_id(url) is not None


def is_youtube_playlist(url: str) -> bool:
    return extract_playlist_id(url) is not None


def is_youtube_homepage(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    return host == "youtube.com" and parsed.path.rstrip("/") in {"", "/"}


def get_youtube_transcript(
    url: str, timeout: int = 15, force_asr: bool = False
) -> Optional[str]:
    if force_asr or _env_flag("YOUTUBE_TRANSCRIPT_FORCE_ASR"):
        transcript = get_youtube_transcript_via_asr(url)
        if transcript:
            return transcript

    transcript = _get_youtube_transcript_via_api(url, timeout=timeout)
    if transcript:
        return transcript
    transcript = get_youtube_transcript_via_ytdlp(url, timeout=max(timeout, 45))
    if transcript:
        return transcript
    if _youtube_transcript_auto_asr_enabled():
        return get_youtube_transcript_via_asr(url)
    return None


def _get_youtube_transcript_via_api(url: str, timeout: int = 15) -> Optional[str]:
    try:
        video_id = extract_video_id(url)
        if not video_id:
            raise ValueError(f"Could not extract video ID from URL: {url}")
        logger.info("Fetching transcript for YouTube video: %s", video_id)

        rate_limiter = get_rate_limiter()
        rate_limiter.wait(url)

        transcript_list = _list_transcripts(video_id)

        for lang in ["ru", "en"]:
            try:
                transcript = transcript_list.find_transcript([lang])
                text = _transcript_to_text(transcript.fetch())
                logger.info(
                    "Found %s transcript for %s: %d chars", lang, video_id, len(text)
                )
                return text
            except Exception as e:
                logger.debug("No %s transcript found: %s", lang, e)
                continue

        try:
            transcript = _find_generated_transcript(transcript_list, ["ru", "en"])
            text = _transcript_to_text(transcript.fetch())
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


def get_youtube_transcript_via_ytdlp(
    url: str, timeout: int = 45, languages: Optional[list[str]] = None
) -> Optional[str]:
    video_id = extract_video_id(url)
    if not video_id:
        return None

    ytdlp = os.environ.get("YT_DLP_PATH") or shutil.which("yt-dlp")
    if not ytdlp:
        logger.warning("yt-dlp fallback skipped: yt-dlp executable not found")
        return None

    lang_list = languages or _youtube_transcript_languages()
    with tempfile.TemporaryDirectory(prefix="article-pipeline-ytdlp-") as tmp_dir:
        output_template = str(Path(tmp_dir) / "%(id)s.%(ext)s")
        for language in lang_list:
            cmd = _build_ytdlp_subtitle_command(ytdlp, output_template, language, url)
            logger.info("Fetching YouTube subtitles via yt-dlp: %s (%s)", video_id, language)
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except Exception as e:
                logger.warning("yt-dlp subtitle fallback failed for %s: %s", video_id, e)
                continue

            transcript = _read_ytdlp_subtitle_text(Path(tmp_dir), video_id, language)
            if transcript:
                logger.info(
                    "Fetched YouTube subtitles via yt-dlp for %s: %d chars",
                    video_id,
                    len(transcript),
                )
                return transcript

            output = "\n".join(
                part.strip()
                for part in [completed.stdout, completed.stderr]
                if part and part.strip()
            )
            if completed.returncode != 0:
                logger.warning(
                    "yt-dlp subtitle fallback returned %s for %s (%s): %s",
                    completed.returncode,
                    video_id,
                    language,
                    output[-500:],
                )
            else:
                logger.debug(
                    "yt-dlp subtitle fallback produced no text for %s (%s): %s",
                    video_id,
                    language,
                    output[-500:],
                )
    return None


def _youtube_transcript_languages() -> list[str]:
    raw = os.environ.get("YOUTUBE_TRANSCRIPT_LANGS", "ru,en-orig,en")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _build_ytdlp_subtitle_command(
    ytdlp: str, output_template: str, language: str, url: str
) -> list[str]:
    cmd = [
        ytdlp,
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        language,
        "--sub-format",
        "json3",
        "--output",
        output_template,
    ]
    _extend_ytdlp_env_args(cmd)
    cmd.append(url)
    return cmd


def _extend_ytdlp_env_args(cmd: list[str]) -> None:
    if os.environ.get("YT_DLP_REMOTE_COMPONENTS"):
        cmd.extend(["--remote-components", os.environ["YT_DLP_REMOTE_COMPONENTS"]])
    if os.environ.get("YT_DLP_IMPERSONATE"):
        cmd.extend(["--impersonate", os.environ["YT_DLP_IMPERSONATE"]])
    if os.environ.get("YT_DLP_COOKIES_FROM_BROWSER"):
        cmd.extend(["--cookies-from-browser", os.environ["YT_DLP_COOKIES_FROM_BROWSER"]])
    if os.environ.get("YT_DLP_COOKIES_FILE"):
        cmd.extend(["--cookies", os.environ["YT_DLP_COOKIES_FILE"]])
    proxy = (
        os.environ.get("YT_DLP_PROXY")
        or os.environ.get("YOUTUBE_TRANSCRIPT_HTTPS_PROXY")
        or os.environ.get("YOUTUBE_TRANSCRIPT_HTTP_PROXY")
    )
    if proxy:
        cmd.extend(["--proxy", proxy])
    if os.environ.get("YT_DLP_SLEEP_SUBTITLES"):
        cmd.extend(["--sleep-subtitles", os.environ["YT_DLP_SLEEP_SUBTITLES"]])
    if os.environ.get("YT_DLP_EXTRA_ARGS"):
        cmd.extend(shlex.split(os.environ["YT_DLP_EXTRA_ARGS"]))


def _read_ytdlp_subtitle_text(
    folder: Path, video_id: str, language: str
) -> Optional[str]:
    preferred = list(folder.glob(f"{video_id}.{language}.json3"))
    candidates = preferred or sorted(folder.glob(f"{video_id}.*.json3"))
    for path in candidates:
        text = _json3_subtitle_to_text(path)
        if text:
            return text
    return None


def _json3_subtitle_to_text(path: Path) -> Optional[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse yt-dlp subtitle file %s: %s", path.name, e)
        return None

    lines: list[str] = []
    last = ""
    for event in data.get("events", []):
        text = "".join(str(seg.get("utf8", "")) for seg in event.get("segs", []))
        text = _single_line(text)
        if text and text != last:
            lines.append(text)
            last = text
    return "\n".join(lines) if lines else None


def get_youtube_transcript_via_asr(url: str) -> Optional[str]:
    video_id = extract_video_id(url)
    if not video_id:
        return None

    ytdlp = os.environ.get("YT_DLP_PATH") or shutil.which("yt-dlp")
    asr_backend = _resolve_asr_backend()
    if not ytdlp:
        logger.warning("ASR fallback skipped: yt-dlp executable not found")
        return None
    if not asr_backend:
        logger.warning("ASR fallback skipped: no local ASR backend found")
        return None

    with tempfile.TemporaryDirectory(prefix="article-pipeline-youtube-asr-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        audio = _download_youtube_audio_for_asr(ytdlp, url, tmp_path)
        if not audio:
            return None
        backend_name, backend_command = asr_backend
        transcript = _transcribe_audio_with_asr(
            backend_name, backend_command, audio, tmp_path
        )
        if transcript:
            logger.info(
                "Transcribed YouTube audio via %s for %s: %d chars",
                backend_name,
                video_id,
                len(transcript),
            )
        return transcript


def _resolve_asr_backend() -> Optional[tuple[str, str]]:
    custom = os.environ.get("YOUTUBE_TRANSCRIPT_ASR_COMMAND")
    if custom:
        return ("custom ASR", custom)

    for name, env_name, binary in [
        ("whisper-ctranslate2", "WHISPER_CTRANSLATE2_PATH", "whisper-ctranslate2"),
        ("faster-whisper", "FASTER_WHISPER_PATH", "faster-whisper"),
        ("whisper", "WHISPER_PATH", "whisper"),
    ]:
        command = os.environ.get(env_name) or shutil.which(binary)
        if command:
            return (name, command)
    return None


def _download_youtube_audio_for_asr(ytdlp: str, url: str, folder: Path) -> Optional[Path]:
    output_template = str(folder / "%(id)s.%(ext)s")
    cmd = [
        ytdlp,
        "-f",
        os.environ.get("YOUTUBE_TRANSCRIPT_ASR_FORMAT", "bestaudio/best"),
        "--extract-audio",
        "--audio-format",
        os.environ.get("YOUTUBE_TRANSCRIPT_ASR_AUDIO_FORMAT", "mp3"),
        "--output",
        output_template,
    ]
    if os.environ.get("YOUTUBE_TRANSCRIPT_ASR_AUDIO_QUALITY"):
        cmd.extend(["--audio-quality", os.environ["YOUTUBE_TRANSCRIPT_ASR_AUDIO_QUALITY"]])
    if os.environ.get("YOUTUBE_TRANSCRIPT_ASR_MAX_SECONDS"):
        seconds = os.environ["YOUTUBE_TRANSCRIPT_ASR_MAX_SECONDS"]
        cmd.extend(["--download-sections", f"*00:00-{seconds}"])
    _extend_ytdlp_env_args(cmd)
    cmd.append(url)

    timeout = int(os.environ.get("YOUTUBE_TRANSCRIPT_ASR_DOWNLOAD_TIMEOUT", "600"))
    logger.info("Downloading YouTube audio for ASR: %s", url[:80])
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        logger.warning("Failed to download YouTube audio for ASR: %s", e)
        return None

    audio_files: list[Path] = []
    for extension in ["mp3", "m4a", "opus", "webm", "wav"]:
        audio_files.extend(sorted(folder.glob(f"*.{extension}")))
    if audio_files:
        return audio_files[0]
    output = "\n".join(
        part.strip()
        for part in [completed.stdout, completed.stderr]
        if part and part.strip()
    )
    logger.warning(
        "yt-dlp audio download for ASR returned %s: %s",
        completed.returncode,
        output[-500:],
    )
    return None


def _transcribe_audio_with_asr(
    backend_name: str, backend_command: str, audio: Path, output_dir: Path
) -> Optional[str]:
    if backend_name == "custom ASR":
        cmd = _build_custom_asr_command(backend_command, audio, output_dir)
    else:
        cmd = _build_whisper_compatible_asr_command(
            backend_name, backend_command, audio, output_dir
        )

    timeout = int(os.environ.get("YOUTUBE_TRANSCRIPT_ASR_TIMEOUT", "7200"))
    logger.info("Transcribing YouTube audio via %s: %s", backend_name, audio.name)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        logger.warning("%s failed for %s: %s", backend_name, audio.name, e)
        return None

    text = _read_asr_text_output(output_dir, audio.stem)
    if text:
        return text
    if backend_name == "custom ASR" and completed.stdout.strip():
        return completed.stdout.strip()
    output = "\n".join(
        part.strip()
        for part in [completed.stdout, completed.stderr]
        if part and part.strip()
    )
    logger.warning(
        "%s returned %s without transcript text: %s",
        backend_name,
        completed.returncode,
        output[-500:],
    )
    return None


def _build_custom_asr_command(template: str, audio: Path, output_dir: Path) -> list[str]:
    command = template.format(
        audio=str(audio),
        output_dir=str(output_dir),
        stem=audio.stem,
    )
    return shlex.split(command)


def _build_whisper_compatible_asr_command(
    backend_name: str, command: str, audio: Path, output_dir: Path
) -> list[str]:
    cmd = [
        command,
        str(audio),
        "--model",
        _asr_model_for_backend(backend_name),
        "--output_format",
        "txt",
        "--output_dir",
        str(output_dir),
    ]
    if backend_name == "whisper":
        cmd.extend(["--verbose", "False"])

    language = os.environ.get("YOUTUBE_TRANSCRIPT_ASR_LANGUAGE") or os.environ.get(
        "WHISPER_LANGUAGE"
    )
    if language:
        cmd.extend(["--language", language])
    device = os.environ.get("YOUTUBE_TRANSCRIPT_ASR_DEVICE") or os.environ.get(
        "WHISPER_DEVICE"
    )
    if device:
        cmd.extend(["--device", device])

    extra_args = (
        os.environ.get("YOUTUBE_TRANSCRIPT_ASR_EXTRA_ARGS")
        or os.environ.get("WHISPER_EXTRA_ARGS")
    )
    if extra_args:
        cmd.extend(shlex.split(extra_args))
    return cmd


def _asr_model_for_backend(backend_name: str) -> str:
    if backend_name in {"whisper-ctranslate2", "faster-whisper"}:
        return os.environ.get("FASTER_WHISPER_MODEL") or os.environ.get(
            "WHISPER_MODEL", "large-v3-turbo"
        )
    return os.environ.get("WHISPER_MODEL", "turbo")


def _read_asr_text_output(output_dir: Path, stem: str) -> Optional[str]:
    preferred = output_dir / f"{stem}.txt"
    candidates = [preferred] if preferred.exists() else []
    candidates.extend(path for path in sorted(output_dir.glob("*.txt")) if path != preferred)
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            return text
    return None


def _youtube_transcript_auto_asr_enabled() -> bool:
    if "YOUTUBE_TRANSCRIPT_AUTO_ASR" in os.environ:
        return _env_flag("YOUTUBE_TRANSCRIPT_AUTO_ASR")
    if "YOUTUBE_TRANSCRIPT_ASR" in os.environ:
        return _env_flag("YOUTUBE_TRANSCRIPT_ASR")
    return True


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _list_transcripts(video_id: str):
    proxy_config = _youtube_transcript_proxy_config()
    if proxy_config is not None:
        return YouTubeTranscriptApi(proxy_config=proxy_config).list(video_id)
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
        return YouTubeTranscriptApi.list_transcripts(video_id)
    return YouTubeTranscriptApi().list(video_id)


def _youtube_transcript_proxy_config():
    webshare_username = os.environ.get("YOUTUBE_TRANSCRIPT_WEBSHARE_USERNAME")
    webshare_password = os.environ.get("YOUTUBE_TRANSCRIPT_WEBSHARE_PASSWORD")
    if webshare_username and webshare_password:
        countries = [
            item.strip()
            for item in os.environ.get("YOUTUBE_TRANSCRIPT_WEBSHARE_COUNTRIES", "").split(",")
            if item.strip()
        ]
        return WebshareProxyConfig(
            proxy_username=webshare_username,
            proxy_password=webshare_password,
            filter_ip_locations=countries or None,
        )

    http_proxy = os.environ.get("YOUTUBE_TRANSCRIPT_HTTP_PROXY")
    https_proxy = os.environ.get("YOUTUBE_TRANSCRIPT_HTTPS_PROXY")
    if http_proxy or https_proxy:
        return GenericProxyConfig(http_url=http_proxy, https_url=https_proxy)
    return None


def _find_generated_transcript(transcript_list, languages: list[str]):
    try:
        return transcript_list.find_generated_transcript(languages)
    except TypeError:
        return transcript_list.find_generated_transcript()


def _transcript_to_text(fetched_transcript) -> str:
    if hasattr(fetched_transcript, "to_raw_data"):
        fetched_transcript = fetched_transcript.to_raw_data()

    lines: list[str] = []
    for item in fetched_transcript:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = str(getattr(item, "text", "") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def get_youtube_video_metadata(
    url: str, timeout: int = 15
) -> Optional[YouTubeVideoMetadata]:
    video_id = extract_video_id(url)
    if not video_id:
        return None

    try:
        rate_limiter = get_rate_limiter()
        rate_limiter.wait(url)

        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical_video_url(video_id), "format": "json"},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"},
        )
        response.raise_for_status()
        data = response.json()
        title = str(data.get("title") or "").strip()
        if not title:
            return None
        author = str(data.get("author_name") or "").strip()
        canonical = canonical_video_url(video_id)
        lines = [
            f"Title: {title}",
            f"URL: {canonical}",
            "Transcript: not available",
        ]
        if author:
            lines.insert(1, f"Author: {author}")
        if data.get("thumbnail_url"):
            lines.append(f"Thumbnail: {data['thumbnail_url']}")
        return {"text": "\n".join(lines), "title": title, "canonical_url": canonical}
    except Exception as e:
        logger.warning("Failed to fetch YouTube metadata for %s: %s", url[:80], e)
        return None


def get_youtube_playlist_metadata(
    url: str,
    timeout: int = 15,
    include_transcript_excerpts: bool = False,
    transcript_excerpt_chars: int = 200,
) -> Optional[YouTubePlaylistMetadata]:
    playlist_id = extract_playlist_id(url)
    if not playlist_id:
        return None

    try:
        rate_limiter = get_rate_limiter()
        feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
        rate_limiter.wait(feed_url)
        response = requests.get(
            feed_url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception as e:
        logger.warning("Failed to fetch YouTube playlist feed for %s: %s", url[:80], e)
        return _get_youtube_playlist_oembed(url, playlist_id, timeout=timeout)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    title = (root.findtext("atom:title", namespaces=ns) or "YouTube playlist").strip()
    author = (root.findtext("atom:author/atom:name", namespaces=ns) or "").strip()
    canonical = canonical_playlist_url(playlist_id)
    entries = root.findall("atom:entry", ns)

    lines = [
        f"Title: {title}",
        f"URL: {canonical}",
        f"Playlist ID: {playlist_id}",
    ]
    if author:
        lines.append(f"Author: {author}")
    lines.append(f"Video count: {len(entries)}")
    lines.append("")
    lines.append("Videos:")
    videos: list[YouTubePlaylistVideo] = []

    for index, entry in enumerate(entries, 1):
        video_id = (entry.findtext("yt:videoId", namespaces=ns) or "").strip()
        video_title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        published = (entry.findtext("atom:published", namespaces=ns) or "").strip()
        description = (
            entry.findtext("media:group/media:description", namespaces=ns) or ""
        ).strip()
        video_url = canonical_video_url(video_id) if video_id else ""

        lines.extend(
            [
                "",
                f"{index}. {video_title or video_id}",
                f"URL: {video_url}",
            ]
        )
        if published:
            lines.append(f"Published: {published}")
        clean_description = _single_line(description)[:250] if description else ""
        if description:
            lines.append(f"Description: {clean_description}")

        transcript_excerpt = ""
        if include_transcript_excerpts and video_id:
            transcript = get_youtube_transcript(video_url, timeout=timeout)
            if transcript:
                transcript_excerpt = _single_line(transcript)[:transcript_excerpt_chars]
                lines.append(f"Transcript excerpt: {transcript_excerpt}")
            else:
                lines.append("Transcript excerpt: not available")
        videos.append(
            {
                "index": index,
                "title": video_title or video_id,
                "url": video_url,
                "video_id": video_id,
                "published": published,
                "description": clean_description,
                "transcript_excerpt": transcript_excerpt,
            }
        )

    logger.info(
        "Fetched YouTube playlist %s: %d videos", playlist_id, len(entries)
    )
    return {
        "text": "\n".join(lines).strip(),
        "title": title,
        "canonical_url": canonical,
        "videos": videos,
    }


def _get_youtube_playlist_oembed(
    url: str, playlist_id: str, timeout: int = 15
) -> Optional[YouTubePlaylistMetadata]:
    try:
        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical_playlist_url(playlist_id), "format": "json"},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"},
        )
        response.raise_for_status()
        data = response.json()
        title = str(data.get("title") or "YouTube playlist").strip()
        author = str(data.get("author_name") or "").strip()
        canonical = canonical_playlist_url(playlist_id)
        lines = [
            f"Title: {title}",
            f"URL: {canonical}",
            f"Playlist ID: {playlist_id}",
            "Video list: not available",
        ]
        if author:
            lines.insert(3, f"Author: {author}")
        if data.get("thumbnail_url"):
            lines.append(f"Thumbnail: {data['thumbnail_url']}")
        return {
            "text": "\n".join(lines),
            "title": title,
            "canonical_url": canonical,
            "videos": [],
        }
    except Exception as e:
        logger.warning("Failed to fetch YouTube playlist oEmbed for %s: %s", url[:80], e)
        return None


def canonical_video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def canonical_playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def extract_playlist_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if host not in {"youtube.com", "music.youtube.com"}:
        return None
    if parsed.path.rstrip("/") != "/playlist":
        return None
    playlist_id = parse_qs(parsed.query).get("list", [""])[0].strip()
    return playlist_id or None


def extract_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
        return candidate if VIDEO_ID_RE.match(candidate) else None

    if host not in {"youtube.com", "music.youtube.com"}:
        return None

    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if VIDEO_ID_RE.match(query_id):
        return query_id

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
        candidate = parts[1]
        return candidate if VIDEO_ID_RE.match(candidate) else None

    return None


def _single_line(text: str) -> str:
    return " ".join(text.split()).strip()
