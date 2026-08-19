"""Per-URL failure records, and the retry pass that drains them."""

from unittest.mock import patch

from article_pipeline.config import Config
from article_pipeline.errors import ProcessingError
from article_pipeline.failures import (
    ledger_path,
    load_failures,
    record_failure,
    retry_failures,
    retryable_failures,
    summarize_failures,
)
from article_pipeline.watch import process_inbox_file


def _record(root, url, error, source="a.md"):
    record_failure(root, source_file=source, url=url, title="t", error=error)


def test_a_failure_is_recorded_with_its_retryability(tmp_path):
    _record(tmp_path, "https://a.example/1", ProcessingError.PARSE_ERROR)
    _record(tmp_path, "https://a.example/2", ProcessingError.NOT_FOUND)

    records = {r["url"]: r for r in load_failures(tmp_path)}
    assert records["https://a.example/1"]["retryable"] is True
    assert records["https://a.example/2"]["retryable"] is False
    assert records["https://a.example/2"]["error"] == "not_found"
    assert [r["url"] for r in retryable_failures(tmp_path)] == ["https://a.example/1"]


def test_a_url_that_fails_twice_appears_once_with_its_latest_error(tmp_path):
    _record(tmp_path, "https://a.example/1", ProcessingError.TIMEOUT)
    _record(tmp_path, "https://a.example/1", ProcessingError.BLOCKED)

    records = load_failures(tmp_path)
    assert len(records) == 1
    assert records[0]["error"] == "blocked"
    # Blocked is retryable since the reader-proxy fallback can fetch what 403s us.
    assert records[0]["retryable"] is True


def test_a_torn_line_from_a_killed_run_does_not_break_the_ledger(tmp_path):
    _record(tmp_path, "https://a.example/1", ProcessingError.TIMEOUT)
    with ledger_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"url": "https://a.example/2", "err')

    assert [r["url"] for r in load_failures(tmp_path)] == ["https://a.example/1"]


def test_summarize_counts_by_error(tmp_path):
    _record(tmp_path, "https://a.example/1", ProcessingError.BLOCKED)
    _record(tmp_path, "https://a.example/2", ProcessingError.BLOCKED)
    _record(tmp_path, "https://a.example/3", ProcessingError.NOT_FOUND)
    assert summarize_failures(load_failures(tmp_path)) == "2 blocked, 1 not_found"


def test_a_partly_failing_file_still_succeeds_but_records_the_failure(tmp_path):
    """The hole this closes: nine failures in a ten-link file left no trace."""
    root = tmp_path
    for folder in ("01_inbox", "02_processing", "03_success", "04_failed"):
        (root / folder).mkdir()
    source = root / "01_inbox" / "two-links.md"
    source.write_text(
        "https://good.example/a\nhttps://bad.example/b\n", encoding="utf-8"
    )

    def fake_process(url, title, cfg, engine, force=False):
        if "good" in url:
            return True, ProcessingError.UNKNOWN
        return False, ProcessingError.PARSE_ERROR

    with patch("article_pipeline.watch.process_article", side_effect=fake_process):
        ok = process_inbox_file(source, root, Config(), engine=None)

    assert ok is True, "one good link still makes the file a success"
    assert (root / "03_success" / "two-links.md").exists()
    records = load_failures(root)
    assert [r["url"] for r in records] == ["https://bad.example/b"]
    assert records[0]["source_file"] == "two-links.md"


def test_a_wholly_failing_file_lands_in_failed_with_a_named_suffix(tmp_path):
    """22 of 23 files in 04_failed/ used to carry no suffix at all."""
    root = tmp_path
    for folder in ("01_inbox", "02_processing", "03_success", "04_failed"):
        (root / folder).mkdir()
    source = root / "01_inbox" / "bad.md"
    source.write_text("https://bad.example/b\n", encoding="utf-8")

    with patch(
        "article_pipeline.watch.process_article",
        return_value=(False, ProcessingError.BLOCKED),
    ):
        ok = process_inbox_file(source, root, Config(), engine=None)

    assert ok is False
    landed = list((root / "04_failed").iterdir())
    assert len(landed) == 1
    assert "_error_blocked_" in landed[0].name


def test_retry_reruns_only_the_retryable_and_drains_what_succeeds(tmp_path):
    _record(tmp_path, "https://a.example/recovers", ProcessingError.PARSE_ERROR)
    _record(tmp_path, "https://a.example/persists", ProcessingError.TIMEOUT)
    _record(tmp_path, "https://a.example/gone", ProcessingError.NOT_FOUND)

    tried = []

    def fake_process(url, title, cfg, engine, force=False):
        tried.append(url)
        if "recovers" in url:
            return True, ProcessingError.UNKNOWN
        return False, ProcessingError.TIMEOUT

    with patch("article_pipeline.processor.process_article", side_effect=fake_process):
        recovered, remaining = retry_failures(tmp_path, Config(), engine=None)

    assert "https://a.example/gone" not in tried, "a 404 will not become a 200"
    assert sorted(tried) == ["https://a.example/persists", "https://a.example/recovers"]
    assert recovered == 1
    assert remaining == 2

    left = {r["url"] for r in load_failures(tmp_path)}
    assert left == {"https://a.example/persists", "https://a.example/gone"}


def test_an_all_clear_retry_removes_the_ledger(tmp_path):
    _record(tmp_path, "https://a.example/1", ProcessingError.PARSE_ERROR)
    with patch(
        "article_pipeline.processor.process_article",
        return_value=(True, ProcessingError.UNKNOWN),
    ):
        recovered, remaining = retry_failures(tmp_path, Config(), engine=None)

    assert (recovered, remaining) == (1, 0)
    assert not ledger_path(tmp_path).exists()


def test_retry_on_an_empty_ledger_is_a_no_op(tmp_path):
    assert retry_failures(tmp_path, Config(), engine=None) == (0, 0)


def test_a_crash_mid_retry_loses_at_most_twenty_urls_of_progress(tmp_path):
    """The 2026-08-19 flush: a pass over 3781 URLs died before the end-of-run
    rewrite, so the next pass re-fetched and re-summarized all of them."""
    import pytest

    urls = [f"https://a.example/{i:02d}" for i in range(25)]
    for url in urls:
        _record(tmp_path, url, ProcessingError.PARSE_ERROR)

    def fake_process(url, title, cfg, engine, force=False):
        if url == urls[22]:
            raise RuntimeError("machine went to sleep")
        return True, ProcessingError.UNKNOWN

    with patch("article_pipeline.processor.process_article", side_effect=fake_process):
        with pytest.raises(RuntimeError):
            retry_failures(tmp_path, Config(), engine=None)

    left = [r["url"] for r in load_failures(tmp_path)]
    assert left == urls[20:], "the checkpoint at 20 must survive the crash"
