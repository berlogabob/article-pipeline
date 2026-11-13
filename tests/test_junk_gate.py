"""Error pages and link shells must become terminal failures, not notes.

The 2026-08-19 backlog flush saved 404 pages as articles — summarized in
earnest, tagged "ошибка", and auto-related to each other.
"""

from unittest.mock import patch

from article_pipeline.config import Config
from article_pipeline.errors import ProcessingError, is_retryable
from article_pipeline.processor import junk_reason, process_article


def test_error_page_titles_are_junk():
    assert junk_reason("404: Page not found - Machine Learns", "https://a.example/x")
    assert junk_reason("403 Forbidden", "https://a.example/x")
    assert junk_reason("Just a moment...", "https://a.example/x")
    assert junk_reason("URL Source", "https://svn.example/raw")
    assert junk_reason("Страница не найдена", "https://a.example/x")


def test_shell_and_asset_urls_are_junk():
    assert junk_reason("Verifying Device", "https://www.google.com/share.google?q=abc")
    assert junk_reason("HF-Mirror", "https://hf-mirror.com/")
    assert junk_reason("t", "https://cdn.example.com/photo.jpg")


def test_share_google_with_real_content_passes():
    """The interstitial sometimes exposes the real article — those saves stay."""
    assert junk_reason(
        "Asterinas 0.18 Released For Rust-Written OS",
        "https://www.google.com/share.google?q=abc",
    ) is None


def test_real_articles_pass():
    assert junk_reason("OverFlow: Putting flows on top of neural transducers", "https://arxiv.org/abs/2211.06045") is None
    assert junk_reason("Понимание ошибок 404 в веб-разработке", "https://habr.com/p/1") is None


def test_process_article_records_a_404_page_as_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_PIPELINE_JUNK_LEDGER", str(tmp_path / "junk.jsonl"))
    cfg = Config()
    cfg.output_format = "typst"
    cfg.typst.vault_dir = str(tmp_path / "vault")
    articles = tmp_path / "vault" / cfg.typst.articles_subdir
    articles.mkdir(parents=True)

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(
            {
                "expanded_url": "https://erogol.com/gone",
                "normalized_url": "https://erogol.com/gone",
                "resolved_title": "404: Page not found - Machine Learns",
                "extracted_text": "Whoops... this URL doesn't exist. " * 20,
                "image_urls": [],
                "is_youtube": False,
                "source": "erogol.com",
                "canonical_url": "",
                "metadata_only": False,
            },
            ProcessingError.UNKNOWN,
        ),
    ):
        ok, err = process_article("https://erogol.com/gone", "t", cfg, engine=None)

    assert ok is False
    assert err is ProcessingError.NOT_FOUND
    assert "https://erogol.com/gone" in (tmp_path / "junk.jsonl").read_text()
    assert not is_retryable(err), "a 404 page must not haunt the retry ledger"
    assert list(articles.iterdir()) == [], "an error page must leave no note behind"


def test_force_overrides_the_gate(tmp_path):
    """--force exists precisely to rescue a page the heuristics misjudge."""
    cfg = Config()
    cfg.output_format = "typst"
    cfg.typst.vault_dir = str(tmp_path / "vault")
    (tmp_path / "vault" / cfg.typst.articles_subdir).mkdir(parents=True)

    with patch(
        "article_pipeline.processor.ingest_article",
        return_value=(
            {
                "expanded_url": "https://erogol.com/gone",
                "normalized_url": "https://erogol.com/gone",
                "resolved_title": "404: Page not found",
                "extracted_text": "body",
                "image_urls": [],
                "is_youtube": False,
                "source": "erogol.com",
                "canonical_url": "",
                "metadata_only": False,
            },
            ProcessingError.UNKNOWN,
        ),
    ), patch("article_pipeline.processor.summarize", return_value=None) as summ:
        ok, err = process_article(
            "https://erogol.com/gone", "t", cfg, engine=None, force=True
        )

    assert summ.called, "with force=True the gate must not short-circuit the pipeline"
    assert err is ProcessingError.LLM_ERROR


def test_lone_surrogates_do_not_crash_the_note_write(tmp_path):
    """One unpaired \\ud835 in extracted text crashed a whole retry run."""
    from unittest.mock import patch as _patch
    from article_pipeline.metadata import ArticleMetadata

    cfg = Config()
    cfg.output_format = "typst"
    cfg.typst.vault_dir = str(tmp_path / "vault")
    articles = tmp_path / "vault" / cfg.typst.articles_subdir
    articles.mkdir(parents=True)

    metadata = ArticleMetadata(
        summary_ru="х",
        tags=["test"],
        author=None,
        verification_notes="средняя",
        is_tutorial=False,
        step_by_step_guidance=None,
    )
    with _patch(
        "article_pipeline.processor.ingest_article",
        return_value=(
            {
                "expanded_url": "https://example.com/math",
                "normalized_url": "https://example.com/math",
                "resolved_title": "Math note",
                "extracted_text": "broken math \ud835 surrogate " * 10,
                "image_urls": [],
                "is_youtube": False,
                "source": "example.com",
                "canonical_url": "",
                "metadata_only": False,
            },
            ProcessingError.UNKNOWN,
        ),
    ), _patch("article_pipeline.processor.summarize", return_value=metadata):
        ok, err = process_article("https://example.com/math", "t", cfg, engine=None)

    assert ok is True, f"expected a saved note, got {err}"
    saved = list(articles.glob("*.typ"))
    assert len(saved) == 1
    assert "\ud835" not in saved[0].read_text(encoding="utf-8")
