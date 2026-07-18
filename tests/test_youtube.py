"""Ported from the restored logseq-processor project's tests/test_youtube.py,
adapted to import from `article_pipeline.youtube` instead of `src.youtube`.
"""

import json
import subprocess
from unittest.mock import Mock, patch

from article_pipeline.youtube import (
    extract_video_id,
    extract_playlist_id,
    get_youtube_playlist_metadata,
    get_youtube_transcript,
    get_youtube_transcript_via_asr,
    get_youtube_video_metadata,
    is_youtube,
    is_youtube_homepage,
    is_youtube_playlist,
)


def test_is_youtube_only_matches_video_urls():
    assert is_youtube("https://www.youtube.com/watch?v=gNZaDBeHgSk")
    assert is_youtube("https://youtu.be/gNZaDBeHgSk")
    assert is_youtube("https://www.youtube.com/shorts/gNZaDBeHgSk")
    assert is_youtube("https://www.youtube.com/live/gNZaDBeHgSk?si=abc")

    assert not is_youtube("https://www.youtube.com/")
    assert not is_youtube("https://www.youtube.com/@pixaroma")
    assert not is_youtube("https://youtube.com/thenewstack?sub_confirmation=1")
    assert not is_youtube("https://www.youtube.com/playlist?list=PLabc")


def test_is_youtube_playlist_matches_playlist_urls():
    assert is_youtube_playlist("https://www.youtube.com/playlist?list=PLabc")
    assert extract_playlist_id("https://www.youtube.com/playlist?list=PLabc") == "PLabc"
    assert not is_youtube_playlist("https://www.youtube.com/watch?v=gNZaDBeHgSk")


def test_extract_video_id_from_supported_youtube_urls():
    assert (
        extract_video_id("https://www.youtube.com/watch?v=gNZaDBeHgSk&t=12")
        == "gNZaDBeHgSk"
    )


def test_is_youtube_homepage_only_matches_root_youtube():
    assert is_youtube_homepage("https://www.youtube.com/")
    assert is_youtube_homepage("https://youtube.com")

    assert not is_youtube_homepage("https://www.youtube.com/@pixaroma")
    assert not is_youtube_homepage("https://www.youtube.com/watch?v=gNZaDBeHgSk")
    assert extract_video_id("https://youtu.be/gNZaDBeHgSk") == "gNZaDBeHgSk"
    assert (
        extract_video_id("https://www.youtube.com/embed/gNZaDBeHgSk")
        == "gNZaDBeHgSk"
    )


def test_get_youtube_video_metadata_uses_oembed():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "title": "Blender 5.1 Features in LESS THAN FIVE MINUTES!",
        "author_name": "SouthernShotty",
        "thumbnail_url": "https://i.ytimg.com/vi/gNZaDBeHgSk/hqdefault.jpg",
    }

    with patch("article_pipeline.youtube.get_rate_limiter") as rate_limiter, patch(
        "article_pipeline.youtube.requests.get", return_value=response
    ) as get:
        metadata = get_youtube_video_metadata(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    rate_limiter.return_value.wait.assert_called_once()
    get.assert_called_once()
    assert metadata is not None
    assert metadata["title"] == "Blender 5.1 Features in LESS THAN FIVE MINUTES!"
    assert metadata["canonical_url"] == "https://www.youtube.com/watch?v=gNZaDBeHgSk"
    assert "Transcript: not available" in metadata["text"]


def test_get_youtube_transcript_supports_current_api_shape():
    class FetchedTranscript:
        def to_raw_data(self):
            return [{"text": "line one"}, {"text": " line two "}]

    class Transcript:
        def fetch(self):
            return FetchedTranscript()

    class TranscriptList:
        def find_transcript(self, languages):
            assert languages == ["ru"]
            return Transcript()

    class TranscriptApi:
        def list(self, video_id):
            assert video_id == "gNZaDBeHgSk"
            return TranscriptList()

    with patch("article_pipeline.youtube.YouTubeTranscriptApi", TranscriptApi), patch(
        "article_pipeline.youtube.get_rate_limiter"
    ) as rate_limiter:
        transcript = get_youtube_transcript(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    rate_limiter.return_value.wait.assert_called_once()
    assert transcript == "line one\nline two"


def test_get_youtube_transcript_falls_back_to_ytdlp_json3():
    class TranscriptApi:
        def list(self, video_id):
            raise RuntimeError("IP blocked")

    def fake_run(cmd, capture_output, text, timeout, check):
        output_dir = cmd[cmd.index("--output") + 1].rsplit("/", 1)[0]
        subtitle = {
            "events": [
                {"segs": [{"utf8": "first "}, {"utf8": "line"}]},
                {"segs": [{"utf8": "second\nline"}]},
            ]
        }
        path = f"{output_dir}/gNZaDBeHgSk.ru.json3"
        with open(path, "w", encoding="utf-8") as file:
            json.dump(subtitle, file)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("article_pipeline.youtube.YouTubeTranscriptApi", TranscriptApi), patch(
        "article_pipeline.youtube.get_rate_limiter"
    ), patch("article_pipeline.youtube.shutil.which", return_value="/bin/yt-dlp"), patch(
        "article_pipeline.youtube.subprocess.run", side_effect=fake_run
    ) as run:
        transcript = get_youtube_transcript(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    run.assert_called_once()
    assert transcript == "first line\nsecond line"


def test_get_youtube_transcript_uses_generic_proxy_env(monkeypatch):
    class FetchedTranscript:
        def to_raw_data(self):
            return [{"text": "proxied line"}]

    class Transcript:
        def fetch(self):
            return FetchedTranscript()

    class TranscriptList:
        def find_transcript(self, languages):
            return Transcript()

    class TranscriptApi:
        def __init__(self, proxy_config=None):
            self.proxy_config = proxy_config

        def list(self, video_id):
            assert self.proxy_config is not None
            assert self.proxy_config.to_requests_dict() == {
                "http": "http://proxy.local:8080",
                "https": "http://proxy.local:8080",
            }
            return TranscriptList()

    monkeypatch.setenv("YOUTUBE_TRANSCRIPT_HTTP_PROXY", "http://proxy.local:8080")
    monkeypatch.setenv("YOUTUBE_TRANSCRIPT_HTTPS_PROXY", "http://proxy.local:8080")
    with patch("article_pipeline.youtube.YouTubeTranscriptApi", TranscriptApi), patch(
        "article_pipeline.youtube.get_rate_limiter"
    ):
        transcript = get_youtube_transcript(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    assert transcript == "proxied line"


def test_get_youtube_transcript_via_asr_downloads_audio_and_runs_local_asr(tmp_path):
    def fake_run(cmd, capture_output, text, timeout, check):
        if "--extract-audio" in cmd:
            output_dir = cmd[cmd.index("--output") + 1].rsplit("/", 1)[0]
            with open(f"{output_dir}/gNZaDBeHgSk.wav", "w", encoding="utf-8") as file:
                file.write("audio")
        else:
            output_dir = cmd[cmd.index("--output_dir") + 1]
            with open(f"{output_dir}/gNZaDBeHgSk.txt", "w", encoding="utf-8") as file:
                file.write("asr transcript")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("article_pipeline.youtube.shutil.which") as which, patch(
        "article_pipeline.youtube.subprocess.run", side_effect=fake_run
    ) as run:
        which.side_effect = lambda name: f"/bin/{name}"
        transcript = get_youtube_transcript_via_asr(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    assert run.call_count == 2
    assert transcript == "asr transcript"


def test_get_youtube_transcript_runs_asr_when_captions_are_missing():
    class TranscriptApi:
        def list(self, video_id):
            raise RuntimeError("IP blocked")

    with patch("article_pipeline.youtube.YouTubeTranscriptApi", TranscriptApi), patch(
        "article_pipeline.youtube.get_rate_limiter"
    ), patch(
        "article_pipeline.youtube.get_youtube_transcript_via_ytdlp", return_value=None
    ), patch(
        "article_pipeline.youtube.get_youtube_transcript_via_asr",
        return_value="created transcript",
    ) as asr:
        transcript = get_youtube_transcript(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    asr.assert_called_once_with("https://www.youtube.com/watch?v=gNZaDBeHgSk")
    assert transcript == "created transcript"


def test_get_youtube_transcript_can_disable_automatic_asr(monkeypatch):
    class TranscriptApi:
        def list(self, video_id):
            raise RuntimeError("IP blocked")

    monkeypatch.setenv("YOUTUBE_TRANSCRIPT_AUTO_ASR", "0")
    with patch("article_pipeline.youtube.YouTubeTranscriptApi", TranscriptApi), patch(
        "article_pipeline.youtube.get_rate_limiter"
    ), patch(
        "article_pipeline.youtube.get_youtube_transcript_via_ytdlp", return_value=None
    ), patch(
        "article_pipeline.youtube.get_youtube_transcript_via_asr"
    ) as asr:
        transcript = get_youtube_transcript(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk"
        )

    asr.assert_not_called()
    assert transcript is None


def test_get_youtube_transcript_force_asr_uses_asr_first():
    class TranscriptApi:
        def list(self, video_id):
            raise AssertionError("caption API should not run when ASR succeeds")

    with patch("article_pipeline.youtube.YouTubeTranscriptApi", TranscriptApi), patch(
        "article_pipeline.youtube.get_youtube_transcript_via_asr",
        return_value="forced transcript",
    ) as asr:
        transcript = get_youtube_transcript(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk",
            force_asr=True,
        )

    asr.assert_called_once_with("https://www.youtube.com/watch?v=gNZaDBeHgSk")
    assert transcript == "forced transcript"


def test_get_youtube_playlist_metadata_uses_rss_feed():
    response = Mock()
    response.raise_for_status.return_value = None
    response.text = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/"
          xmlns="http://www.w3.org/2005/Atom">
      <title>Курс по Obsidian (9 видео)</title>
      <author><name>Теплица социальных технологий</name></author>
      <entry>
        <yt:videoId>CKRgUveNZx8</yt:videoId>
        <title>Obsidian уроки #1</title>
        <published>2022-04-12T20:28:19+00:00</published>
        <media:group><media:description>Установка Obsidian</media:description></media:group>
      </entry>
      <entry>
        <yt:videoId>ye9YMLQ8hY0</yt:videoId>
        <title>Obsidian уроки #2</title>
      </entry>
    </feed>
    """

    with patch("article_pipeline.youtube.get_rate_limiter") as rate_limiter, patch(
        "article_pipeline.youtube.requests.get", return_value=response
    ) as get, patch(
        "article_pipeline.youtube.get_youtube_transcript", return_value="текст урока"
    ) as transcript:
        metadata = get_youtube_playlist_metadata(
            "https://www.youtube.com/playlist?list=PLeDR6",
            include_transcript_excerpts=True,
        )

    rate_limiter.return_value.wait.assert_called_once()
    get.assert_called_once()
    assert transcript.call_count == 2
    assert metadata is not None
    assert metadata["title"] == "Курс по Obsidian (9 видео)"
    assert metadata["canonical_url"] == "https://www.youtube.com/playlist?list=PLeDR6"
    assert "Video count: 2" in metadata["text"]
    assert "Obsidian уроки #1" in metadata["text"]
    assert "Transcript excerpt: текст урока" in metadata["text"]
    assert len(metadata["videos"]) == 2
    assert metadata["videos"][0]["index"] == 1
    assert metadata["videos"][0]["url"] == "https://www.youtube.com/watch?v=CKRgUveNZx8"
    assert metadata["videos"][0]["transcript_excerpt"] == "текст урока"
