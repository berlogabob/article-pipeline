"""Tests for resolved-title precedence in stage1_ingest.ingest_article and
processor.process_article.

Ported from the restored logseq-processor project's tests/test_processor_titles.py,
including the YouTube/playlist cases (adapted to this project's stage1_ingest +
processor module layout and typst/markdown output formats).
"""

from unittest.mock import patch

from article_pipeline.config import Config
from article_pipeline.metadata import ArticleMetadata
from article_pipeline.net import normalize_url
from article_pipeline.processor import process_article
from article_pipeline.stage1_ingest import ingest_article


def test_ingest_article_returns_resolved_html_title():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        fetch_article.return_value = {
            "text": "Extracted article body long enough",
            "title": "Original Article Title",
            "canonical_url": None,
        }

        ingest, err = ingest_article("https://example.com/slug", "slug")

    assert err.value == "unknown"
    assert ingest is not None
    assert ingest["resolved_title"] == "Original Article Title"


def test_ingest_article_resolved_title_empty_when_extractor_has_no_title():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        fetch_article.return_value = {
            "text": "Extracted article body long enough",
            "title": None,
            "canonical_url": None,
        }

        ingest, err = ingest_article("https://example.com/slug", "slug")

    assert err.value == "unknown"
    assert ingest is not None
    assert ingest["resolved_title"] == ""


def test_ingest_article_uses_canonical_url_for_final_url_and_dedup():
    with patch(
        "article_pipeline.stage1_ingest.expand_url",
        side_effect=lambda url: url,
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        fetch_article.return_value = {
            "text": "Extracted article body long enough",
            "title": "Original Article Title",
            "canonical_url": "https://target.example/original-article",
        }

        ingest, err = ingest_article("https://share.example/abc", "abc")

    assert err.value == "unknown"
    assert ingest is not None
    assert ingest["expanded_url"] == "https://target.example/original-article"
    assert ingest["canonical_url"] == "https://target.example/original-article"
    assert ingest["normalized_url"] == normalize_url(
        "https://target.example/original-article"
    )
    assert ingest["source"] == "target.example"


def test_ingest_article_canonical_url_empty_when_absent():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        fetch_article.return_value = {
            "text": "Extracted article body long enough",
            "title": "Original Article Title",
            "canonical_url": None,
        }

        ingest, err = ingest_article("https://example.com/slug", "slug")

    assert err.value == "unknown"
    assert ingest is not None
    assert ingest["canonical_url"] == ""


def test_process_article_uses_resolved_title_for_filename_and_note_title(tmp_path):
    metadata = ArticleMetadata(
        summary_ru="Summary",
        tags=["tag"],
        author=None,
        verification_notes="OK",
    )
    cfg = Config()
    cfg.output_format = "markdown"
    cfg.markdown.out_dir = str(tmp_path)

    ingest_result = {
        "expanded_url": "https://example.com/slug",
        "normalized_url": normalize_url("https://example.com/slug"),
        "resolved_title": "Original Article Title",
        "extracted_text": "Extracted article body long enough",
        "image_urls": [],
        "is_youtube": False,
        "source": "example.com",
        "canonical_url": "",
    }

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(ingest_result, None),
    ), patch("article_pipeline.processor.summarize", return_value=metadata):
        ok, err = process_article(
            "https://example.com/slug", "slug", cfg, engine=None, force=True
        )

    assert ok
    assert err.value == "unknown"
    out_path = tmp_path / "example.com - Original Article Title.md"
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "title: Original Article Title" in content
    assert "# Original Article Title" in content


def test_process_article_falls_back_to_passed_title_when_unresolved(tmp_path):
    metadata = ArticleMetadata(
        summary_ru="Summary",
        tags=["tag"],
        author=None,
        verification_notes="OK",
    )
    cfg = Config()
    cfg.output_format = "markdown"
    cfg.markdown.out_dir = str(tmp_path)

    ingest_result = {
        "expanded_url": "https://example.com/slug",
        "normalized_url": normalize_url("https://example.com/slug"),
        "resolved_title": "",
        "extracted_text": "Extracted article body long enough",
        "image_urls": [],
        "is_youtube": False,
        "source": "example.com",
        "canonical_url": "",
    }

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(ingest_result, None),
    ), patch("article_pipeline.processor.summarize", return_value=metadata):
        ok, err = process_article(
            "https://example.com/slug", "slug", cfg, engine=None, force=True
        )

    assert ok
    assert err.value == "unknown"
    out_path = tmp_path / "example.com - slug.md"
    assert out_path.exists()


def test_ingest_article_uses_html_path_for_youtube_channel_urls():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.get_youtube_transcript"
    ) as transcript, patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        fetch_article.return_value = {
            "text": "Channel page body long enough",
            "title": "pixaroma - YouTube",
            "canonical_url": None,
        }

        ingest, err = ingest_article("https://www.youtube.com/@pixaroma", "pixaroma")

    transcript.assert_not_called()
    assert err.value == "unknown"
    assert ingest is not None
    assert not ingest["is_youtube"]
    assert ingest["resolved_title"] == "pixaroma - YouTube"


def test_ingest_article_rejects_youtube_homepage():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        ingest, err = ingest_article("https://www.youtube.com/", "YouTube")

    fetch_article.assert_not_called()
    assert ingest is None
    assert err.value == "invalid_url"


def test_ingest_article_rejects_youtube_video_when_transcript_missing():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.get_youtube_transcript", return_value=None
    ):
        ingest, err = ingest_article(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk", "old title"
        )

    assert err.value == "empty_content"
    assert ingest is None


def test_ingest_article_passes_force_youtube_asr_to_transcript_fetcher():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.get_youtube_transcript",
        return_value="forced transcript text",
    ) as transcript:
        ingest, err = ingest_article(
            "https://www.youtube.com/watch?v=gNZaDBeHgSk",
            "old title",
            force_youtube_asr=True,
        )

    assert err.value == "unknown"
    assert ingest is not None
    transcript.assert_called_once_with(
        "https://www.youtube.com/watch?v=gNZaDBeHgSk",
        force_asr=True,
    )
    assert ingest["extracted_text"] == "forced transcript text"


def test_ingest_article_uses_youtube_playlist_metadata():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda url: url
    ), patch(
        "article_pipeline.stage1_ingest.get_youtube_playlist_metadata"
    ) as playlist_metadata, patch(
        "article_pipeline.stage1_ingest.get_youtube_transcript"
    ) as transcript, patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article"
    ) as fetch_article:
        playlist_metadata.return_value = {
            "text": "playlist text",
            "title": "Курс по Obsidian (9 видео)",
            "canonical_url": "https://www.youtube.com/playlist?list=PLeDR6",
            "videos": [
                {
                    "index": 1,
                    "title": "Урок 1",
                    "url": "https://www.youtube.com/watch?v=CKRgUveNZx8",
                    "video_id": "CKRgUveNZx8",
                    "published": "2022-04-12T02:45:00+00:00",
                    "description": "Описание урока",
                    "transcript_excerpt": "",
                }
            ],
        }

        ingest, err = ingest_article(
            "https://www.youtube.com/playlist?list=PLeDR6", "old title"
        )

    assert err.value == "unknown"
    assert ingest is not None
    assert ingest["is_youtube"]
    assert ingest["expanded_url"] == "https://www.youtube.com/playlist?list=PLeDR6"
    assert ingest["resolved_title"] == "Курс по Obsidian (9 видео)"
    assert ingest["extracted_text"] == "playlist text"
    assert len(ingest["playlist_videos"]) == 1
    transcript.assert_not_called()
    fetch_article.assert_not_called()


def _playlist_ingest_result():
    return {
        "expanded_url": "https://www.youtube.com/playlist?list=PLeDR6",
        "normalized_url": normalize_url(
            "https://www.youtube.com/playlist?list=PLeDR6"
        ),
        "resolved_title": "Курс по Obsidian (9 видео)",
        "extracted_text": "playlist text",
        "image_urls": [],
        "is_youtube": True,
        "source": "youtube.com",
        "canonical_url": "https://www.youtube.com/playlist?list=PLeDR6",
        "playlist_videos": [
            {
                "index": 1,
                "title": "Obsidian уроки #1",
                "url": "https://www.youtube.com/watch?v=CKRgUveNZx8",
                "video_id": "CKRgUveNZx8",
                "published": "2022-04-12T02:45:00+00:00",
                "description": "Установка Obsidian",
                "transcript_excerpt": "",
            },
            {
                "index": 2,
                "title": "Obsidian уроки #2",
                "url": "https://www.youtube.com/watch?v=ye9YMLQ8hY0",
                "video_id": "ye9YMLQ8hY0",
                "published": "",
                "description": "Интерфейс и Markdown",
                "transcript_excerpt": "",
            },
        ],
    }


def test_process_article_creates_child_notes_for_playlist_videos(tmp_path):
    parent_metadata = ArticleMetadata(
        summary_ru="Playlist summary",
        tags=["Obsidian", "базы знаний"],
        author="Teacher",
        verification_notes="OK",
        is_tutorial=True,
        step_by_step_guidance="1. Watch lessons",
    )
    cfg = Config()
    cfg.output_format = "markdown"
    cfg.markdown.out_dir = str(tmp_path)

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(_playlist_ingest_result(), None),
    ), patch("article_pipeline.processor.summarize", return_value=parent_metadata):
        ok, err = process_article(
            "https://www.youtube.com/playlist?list=PLeDR6",
            "Курс",
            cfg,
            engine=None,
            force=True,
        )

    assert ok
    assert err.value == "unknown"

    files = sorted(tmp_path.glob("*.md"))
    assert len(files) == 3

    child_files = [f for f in files if " - 01 - " in f.name or " - 02 - " in f.name]
    assert len(child_files) == 2
    parent_file = next(f for f in files if f not in child_files)
    parent_stem = parent_file.stem

    first_child = next(f for f in child_files if " - 01 - " in f.name)
    content = first_child.read_text(encoding="utf-8")
    assert "url: https://www.youtube.com/watch?v=CKRgUveNZx8" in content
    assert "- Obsidian" in content
    assert "- базы-знаний" in content
    assert "- youtube" in content
    assert "- video" in content
    assert "Course: Курс по Obsidian" in content
    assert f"Course note: [[{parent_stem}]]" in content
    assert "Lesson: 01" in content


def test_process_article_creates_typst_child_notes_linked_to_parent(tmp_path):
    parent_metadata = ArticleMetadata(
        summary_ru="Playlist summary",
        tags=["Obsidian"],
        author="Teacher",
        verification_notes="OK",
        is_tutorial=True,
        step_by_step_guidance="1. Watch lessons",
    )
    cfg = Config()
    cfg.output_format = "typst"
    cfg.typst.vault_dir = str(tmp_path)
    cfg.typst.download_images = False

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(_playlist_ingest_result(), None),
    ), patch("article_pipeline.processor.summarize", return_value=parent_metadata):
        ok, err = process_article(
            "https://www.youtube.com/playlist?list=PLeDR6",
            "Курс",
            cfg,
            engine=None,
            force=True,
        )

    assert ok
    assert err.value == "unknown"

    articles_dir = tmp_path / cfg.typst.articles_subdir
    files = sorted(articles_dir.glob("*.typ"))
    assert len(files) == 3

    child_files = [f for f in files if " - 01 - " in f.name or " - 02 - " in f.name]
    assert len(child_files) == 2
    parent_file = next(f for f in files if f not in child_files)
    parent_id_match = None
    for line in parent_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            parent_id_match = stripped.split('"')[1]
            break
    assert parent_id_match

    first_child = next(f for f in child_files if " - 01 - " in f.name)
    content = first_child.read_text(encoding="utf-8")
    assert '"url": "https://www.youtube.com/watch?v=CKRgUveNZx8"' in content
    assert "== Related" in content
    assert f'#tylog.ref-note("{parent_id_match}")' in content
