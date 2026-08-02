"""An error-shell page must become a failure, never a note (see the 40-note
cleanup of 2026-08-02)."""

from article_pipeline.html_parser import _is_error_shell


def _article(title, text="body " * 50):
    return {"text": text, "title": title, "canonical_url": None}


def test_error_shell_titles_are_rejected():
    assert _is_error_shell(_article("JavaScript is not available."))
    assert _is_error_shell(_article("Google Search"))
    assert _is_error_shell(_article("Just a moment..."))
    assert _is_error_shell(_article("Attention Required! | Cloudflare"))


def test_removed_habr_stub_is_rejected_by_body_marker():
    assert _is_error_shell(
        _article("Хабр", "Материал был снят с публикации модератором.")
    )


def test_real_articles_pass():
    assert not _is_error_shell(_article("How to Build AI Applications"))
    assert not _is_error_shell(_article(None))
