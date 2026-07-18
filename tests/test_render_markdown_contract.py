import unittest
from datetime import datetime as real_datetime
from unittest.mock import patch

from article_pipeline.metadata import ArticleMetadata
from article_pipeline.stage3_render_markdown import build_content, build_props


class _FixedDateTime:
    @classmethod
    def now(cls):
        return real_datetime(2024, 1, 31, 9, 15, 0)


class MetadataMarkdownContractTests(unittest.TestCase):
    def test_build_props_includes_required_logseq_fields_with_tags_and_author(self):
        metadata = ArticleMetadata(
            summary_ru="Кратко",
            tags=["ai", "python"],
            author="Jane Doe",
            verification_notes="Проверено по двум источникам",
            step_by_step_guidance="1) Сделать A\n2) Сделать B",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            props = build_props(
                title="Sample Title",
                url="https://example.com/article",
                res=metadata,
                journal_day="2024-01-30",
            )

        self.assertIn("title:: Sample Title", props)
        self.assertNotIn("alias::", props)
        self.assertIn("type:: article", props)
        self.assertIn("journal-day:: [[2024-01-30]]", props)
        self.assertIn("processed:: 2024-01-31", props)
        self.assertIn("created:: 2024-01-31", props)
        self.assertIn("url:: https://example.com/article", props)
        self.assertIn("tags:: [[ai]] [[python]]", props)
        self.assertIn("author:: Jane Doe", props)

    def test_build_props_omits_author_when_not_provided(self):
        metadata = ArticleMetadata(
            summary_ru="Кратко",
            tags=["notes"],
            author=None,
            verification_notes="Проверено",
            step_by_step_guidance="Шаги",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            props = build_props(
                title="No Author",
                url="https://example.com/no-author",
                res=metadata,
                journal_day="2024-01-31",
            )

        self.assertIn("tags:: [[notes]]", props)
        self.assertNotIn("author::", props)

    def test_build_content_contains_required_sections_and_properties(self):
        metadata = ArticleMetadata(
            summary_ru="Итоговое краткое содержание",
            tags=["knowledge"],
            author=None,
            verification_notes="Проверено вручную",
            step_by_step_guidance="Шаг 1\nШаг 2",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            content = build_content(
                title="Contract Test",
                url="https://example.com/contract",
                metadata=metadata,
                extracted_text="Original extracted text",
                max_len=10_000,
            )

        self.assertIn("title:: Contract Test", content)
        self.assertIn("type:: article", content)
        self.assertIn("journal-day:: [[2024-01-31]]", content)
        self.assertIn("processed:: 2024-01-31", content)
        self.assertIn("created:: 2024-01-31", content)
        self.assertIn("url:: https://example.com/contract", content)

        self.assertIn("**Summary**", content)
        self.assertIn("**Шаг за шагом руководство**", content)
        self.assertIn("**Достоверность**", content)

    def test_build_content_hides_fallback_guidance_placeholder(self):
        metadata = ArticleMetadata(
            summary_ru="Fallback summary",
            tags=["knowledge"],
            author=None,
            verification_notes="Fallback",
            step_by_step_guidance="(не удалось извлечь)",
        )

        with patch("article_pipeline.stage3_render_markdown.datetime", _FixedDateTime):
            content = build_content(
                title="Fallback Contract Test",
                url="https://example.com/fallback-contract",
                metadata=metadata,
                extracted_text="Original extracted text",
                max_len=10_000,
            )

        self.assertNotIn("**Шаг за шагом руководство**", content)
        self.assertIn("**Достоверность**", content)


if __name__ == "__main__":
    unittest.main()
