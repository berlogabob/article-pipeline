import json

import pytest

from article_pipeline import junk_ledger, processor
from article_pipeline.config import Config
from article_pipeline.errors import ProcessingError


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "junk.jsonl"
    monkeypatch.setenv("ARTICLE_PIPELINE_JUNK_LEDGER", str(path))
    return path


def test_missing_file_and_dedup_with_malformed_lines(ledger):
    assert junk_ledger.load_junk() == set()
    ledger.write_text('not json\n[]\n{"url": 7}', encoding="utf-8")
    junk_ledger.record_junk("https://www.example.com/page/", "error page", "note-1")
    junk_ledger.record_junk("https://example.com/page?utm_source=test", "duplicate")
    lines = ledger.read_text().splitlines()
    assert len(lines) == 4
    entry = json.loads(lines[-1])
    assert entry["normalized"] == "https://example.com/page"
    assert entry["reason"] == "error page" and entry["note_id"] == "note-1"
    assert entry["ts"] and entry["source"]
    assert junk_ledger.load_junk() == {"https://example.com/page"}
    ledger.write_text(ledger.read_text() + '{"url":"https://example.com/other"}\n')
    assert "https://example.com/other" in junk_ledger.load_junk()


@pytest.mark.parametrize("url", ["https://example.com/page", "https://www.example.com/page/?utm_source=test"])
def test_junk_stops_before_ingest_and_force_bypasses(ledger, monkeypatch, url):
    junk_ledger.record_junk("https://example.com/page/", "junk")
    def ingest(*args, **kwargs):
        raise AssertionError("ingest called")
    monkeypatch.setattr(processor, "ingest_article", ingest)
    assert processor.process_article(url, "", Config(), None) == (False, ProcessingError.NOT_FOUND)
    with pytest.raises(AssertionError, match="ingest called"):
        processor.process_article(url, "", Config(), None, force=True)
