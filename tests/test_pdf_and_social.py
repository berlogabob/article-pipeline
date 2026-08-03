"""PDF links become articles; social hosts try the reader before settling
for the og-metadata stub."""

import article_pipeline.html_parser as hp
from article_pipeline.pdf import (
    MIN_PDF_TEXT,
    extract_pdf_text,
    looks_like_pdf_url,
    pdf_title,
)


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF with `text` as its content stream, built by hand."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_at).encode() + b"\n%%EOF"
    )
    return out


def test_pdf_url_detection():
    assert looks_like_pdf_url("https://a.example/paper.pdf")
    assert looks_like_pdf_url("https://a.example/x/Paper.PDF?dl=1")
    assert not looks_like_pdf_url("https://arxiv.org/abs/2401.01234")


def test_pdf_text_extraction_and_floor():
    long_text = "Attention is all you need. " * 20  # clears MIN_PDF_TEXT
    assert len(long_text) >= MIN_PDF_TEXT
    extracted = extract_pdf_text(_minimal_pdf(long_text))
    assert extracted and "Attention is all you need." in extracted
    # A near-empty PDF (scanned) yields None, not a junk note.
    assert extract_pdf_text(_minimal_pdf("stub")) is None


def test_pdf_title_falls_back_to_filename():
    data = _minimal_pdf("hello " * 40)
    assert pdf_title(data, "https://a.example/dir/My%20Paper.pdf") == "My Paper.pdf"


def test_social_host_tries_reader_before_metadata_stub(monkeypatch):
    shell = "<html><head><meta property='og:description' content='first tweet only, a stub of decent length'><meta property='og:title' content='Some One on X'></head><body></body></html>"
    monkeypatch.setattr(hp, "fetch_html", lambda url, **kw: shell)
    monkeypatch.setattr(hp, "extract_text_from_html", lambda html, **kw: None)
    full = {"text": "the whole thread", "title": "Thread", "canonical_url": "https://x.com/a/1"}
    calls = []
    monkeypatch.setattr(
        hp, "fetch_reader_article", lambda url, **kw: (calls.append(url), full)[1]
    )
    got = hp.fetch_and_extract_article("https://x.com/a/status/1")
    assert got == full
    assert calls  # reader consulted first

    # Reader fails -> metadata stub still produced, marked metadata_only.
    monkeypatch.setattr(hp, "fetch_reader_article", lambda url, **kw: None)
    got = hp.fetch_and_extract_article("https://x.com/a/status/1")
    assert got is not None and got.get("metadata_only") is True


def test_non_social_host_skips_reader_ordering(monkeypatch):
    monkeypatch.setattr(hp, "fetch_html", lambda url, **kw: "<html></html>")
    monkeypatch.setattr(hp, "extract_text_from_html", lambda html, **kw: None)
    monkeypatch.setattr(
        hp, "fetch_reader_article", lambda url, **kw: {"text": "via reader", "title": "t", "canonical_url": None}
    )
    got = hp.fetch_and_extract_article("https://blog.example/post")
    # Non-social still reaches the reader, but only as the last resort.
    assert got is not None and got["text"] == "via reader"
