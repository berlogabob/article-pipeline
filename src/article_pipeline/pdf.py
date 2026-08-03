"""Fetch and extract text from PDF links (arxiv papers, reports).

Two entry points: `looks_like_pdf_url` for the cheap pre-check, and
`fetch_pdf_article` which downloads, verifies it really is a PDF (magic
bytes, so it also works as a sniffing fallback for extensionless links like
arxiv.org/pdf/...), and extracts text with pypdf.
"""

import io
import logging
import re
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from pypdf import PdfReader

logger = logging.getLogger("article_pipeline")

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArticlePipeline/1.0)"})

MAX_PDF_BYTES = 30 * 1024 * 1024
# Below this the "text" is page furniture, not content — a scanned PDF.
MIN_PDF_TEXT = 200


def looks_like_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def extract_pdf_text(data: bytes) -> Optional[str]:
    """Text of all pages, or None when the PDF has no extractable text."""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:
        logger.warning("pypdf failed: %s", e)
        return None
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    # Collapse the mid-word linebreaks PDF extraction produces.
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text if len(text) >= MIN_PDF_TEXT else None


def pdf_title(data: bytes, url: str) -> str:
    try:
        meta = PdfReader(io.BytesIO(data)).metadata
        if meta and meta.title and meta.title.strip():
            return " ".join(meta.title.split())
    except Exception:
        pass
    return unquote(PurePosixPath(urlparse(url).path).name) or "PDF document"


def fetch_pdf_article(url: str, timeout: int = 30) -> Optional[dict]:
    """Download `url`; if it is a PDF with extractable text, return an
    article dict (text/title/canonical_url) — else None."""
    try:
        resp = _session.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.content[:MAX_PDF_BYTES]
    except Exception as e:
        logger.debug("PDF fetch failed for %s: %s", url[:80], e)
        return None
    if not data.startswith(b"%PDF"):
        return None

    text = extract_pdf_text(data)
    if not text:
        # ponytail: scanned PDFs stay parse_error; wire up MarkItDown/OCR if
        # they ever show up in the ledger in numbers.
        logger.warning("PDF has no extractable text (scanned?): %s", url[:80])
        return None
    logger.info("Extracted %d chars from PDF: %s", len(text), url[:80])
    return {"text": text, "title": pdf_title(data, url), "canonical_url": url}
