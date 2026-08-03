# Capture sources A — PDFs + full social posts

Date: 2026-08-03. Approved by user in session.

## 1. PDF links

Detect a PDF by URL path ending `.pdf` or response `Content-Type:
application/pdf` (arxiv links often lack the extension). Download
size-capped, extract text with `pypdf` (new dependency). Near-empty
extraction (scanned PDF) falls back to the MarkItDown model on the local
oMLX server; if that is off, the URL records `parse_error` as today. Then
the normal summarize → typst path. No images from PDFs.

## 2. X / Threads / Instagram full content

These hosts currently short-circuit to the og-metadata stub before the
reader proxy runs. Reorder: for social hosts try `fetch_reader_article`
(renders JS) first; the metadata stub becomes the fallback instead of the
terminus, keeping its `metadata_only` mark. No syndication API/nitter
unless the reader proves insufficient live.

## Testing

Unit: PDF detection + extraction on a fixture PDF; social-host ordering.
Live: one real X thread, one arxiv PDF, one Instagram post.

## Not doing (YAGNI)

Newsletters (sub-project B, own design), PDF images/OCR beyond MarkItDown,
X syndication API.
