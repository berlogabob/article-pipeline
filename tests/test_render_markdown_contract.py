import unittest
from datetime import datetime as real_datetime
from unittest.mock import patch

import yaml

from article_pipeline.metadata import ArticleMetadata, normalize_tags
from article_pipeline.stage3_render_markdown import build_content, build_frontmatter


class _FixedDateTime:
    @classmethod
    def now(cls):
        return real_datetime(2024, 1, 31, 9, 15, 0)


def _frontmatter(content: str) -> dict:
    assert content.startswith("---\n")
    raw = content.split("---", 2)[1]
    return yaml.safe_load(raw)


class MetadataMarkdownContractTests(unittest.TestCase):
    def test_build_frontmatter_includes_required_obsidian_fields(self):
        metadata = ArticleMetadata(
            summary_ru="Кратко",
            tags=["#AI tools", "Google Drive", "PDF"],
            author="Jane Doe",
            verification_notes="Проверено по двум источникам",
            is_tutorial=True,
            step_by_step_guidance="1. Сделать A\n2. Сделать B",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            frontmatter = build_frontmatter(
                title="Sample Title",
                url="https://example.com/article",
                metadata=metadata,
                source="example.com",
                journal_day="2024-01-30",
            )

        data = _frontmatter(frontmatter)
        self.assertEqual(data["title"], "Sample Title")
        self.assertEqual(data["aliases"], [])
        self.assertEqual(data["type"], "article")
        self.assertEqual(data["journal_day"], "2024-01-30")
        self.assertEqual(data["status"], "processed")
        self.assertEqual(data["read_status"], "unread")
        self.assertEqual(data["processed"], "2024-01-31")
        self.assertEqual(data["created"], "2024-01-31")
        self.assertEqual(data["url"], "https://example.com/article")
        self.assertEqual(data["source"], "example.com")
        self.assertEqual(data["source_url"], "https://example.com")
        self.assertEqual(data["author"], "Jane Doe")
        self.assertEqual(data["tags"], ["AI-tools", "Google-Drive", "PDF"])

    def test_normalize_tags_removes_hashes_spaces_and_duplicates(self):
        self.assertEqual(
            normalize_tags(["#обзоры техники", "Google Drive", "[[LLM]]", "LLM"]),
            ["обзоры-техники", "Google-Drive", "LLM"],
        )

    def test_build_content_includes_tutorial_guidance_only_for_tutorials(self):
        metadata = ArticleMetadata(
            summary_ru="Итоговое краткое содержание",
            tags=["knowledge"],
            author=None,
            verification_notes="Проверено вручную",
            is_tutorial=True,
            step_by_step_guidance="Шаг 1\nШаг 2",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            content = build_content(
                title="Contract Test",
                url="https://example.com/contract",
                metadata=metadata,
                extracted_text="Original extracted text",
                source="example.com",
            )

        data = _frontmatter(content)
        self.assertEqual(data["read_status"], "unread")
        self.assertIn("# Contract Test", content)
        self.assertIn("## Summary", content)
        self.assertIn("## Step-by-step guidance", content)
        self.assertIn("Шаг 1", content)
        self.assertIn("## Verification", content)
        self.assertIn("Original extracted text", content)

    def test_build_content_hides_guidance_for_non_tutorials(self):
        metadata = ArticleMetadata(
            summary_ru="Product announcement summary",
            tags=["news"],
            author=None,
            verification_notes="Средняя достоверность",
            is_tutorial=False,
            step_by_step_guidance="1. Не показывать",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            content = build_content(
                title="Non Tutorial",
                url="https://example.com/non-tutorial",
                metadata=metadata,
                extracted_text="Original extracted text",
            )

        self.assertNotIn("## Step-by-step guidance", content)
        self.assertIn("## Verification", content)

    def test_build_content_hides_empty_tutorial_guidance(self):
        metadata = ArticleMetadata(
            summary_ru="Tutorial without useful steps",
            tags=["howto"],
            author=None,
            verification_notes="Проверено",
            is_tutorial=True,
            step_by_step_guidance="",
        )

        content = build_content(
            title="Empty Guidance",
            url="https://example.com/empty-guidance",
            metadata=metadata,
            extracted_text="Original extracted text",
        )

        self.assertNotIn("## Step-by-step guidance", content)
        self.assertIn("## Verification", content)


if __name__ == "__main__":
    unittest.main()
