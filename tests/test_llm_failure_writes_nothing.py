"""A note the model could not summarize must not reach the vault.

The old behaviour wrote one anyway, with a placeholder body. It synced to
every device and dedup then skipped that URL forever, so the link was lost
in the one way that leaves no trace: it looked processed.
"""

from unittest.mock import patch

import pytest

from article_pipeline.errors import ProcessingError, is_retryable
from article_pipeline.processor import process_article
from article_pipeline.stage2_summarize import summarize


class _UselessEngine:
    """Answers every call with something that will never validate."""

    def __init__(self):
        self.calls = 0

    def chat_json(self, prompt, model, temperature):
        self.calls += 1
        return "I'm afraid I can't do that."


def test_summarize_reports_failure_instead_of_inventing_metadata():
    engine = _UselessEngine()
    result = summarize(
        engine,
        "some article text",
        primary_model="primary",
        fallback_model="fallback",
        temperature_attempts=[0.1, 0.2],
        url="https://example.com/a",
    )
    assert result is None
    assert engine.calls == 4, "both models over both temperatures"


def test_process_article_writes_no_note_when_the_model_fails(tmp_path):
    from article_pipeline.config import Config

    cfg = Config()
    cfg.output_format = "typst"
    cfg.typst.vault_dir = str(tmp_path / "vault")
    articles = tmp_path / "vault" / cfg.typst.articles_subdir
    articles.mkdir(parents=True)

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(
            {
                "expanded_url": "https://example.com/a",
                "normalized_url": "https://example.com/a",
                "resolved_title": "A Title",
                "extracted_text": "body text",
                "image_urls": [],
                "is_youtube": False,
                "source": "example.com",
                "canonical_url": "",
                "metadata_only": False,
            },
            ProcessingError.UNKNOWN,
        ),
    ), patch("article_pipeline.processor.summarize", return_value=None):
        ok, err = process_article("https://example.com/a", "t", cfg, _UselessEngine())

    assert ok is False
    assert err is ProcessingError.LLM_ERROR
    assert list(articles.iterdir()) == [], "a failed summary must leave no note behind"


def test_an_llm_failure_is_worth_retrying():
    """Unlike a 404 — the model may well succeed on the next run."""
    assert is_retryable(ProcessingError.LLM_ERROR)


def test_the_placeholder_metadata_generator_is_gone():
    """Its only caller was the silent-failure path; keeping it invites a relapse."""
    import article_pipeline.metadata as metadata

    assert not hasattr(metadata, "create_fallback_metadata")
