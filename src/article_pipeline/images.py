"""Download article images to a local directory.

New module (no old-project equivalent). Uses net._is_safe_public_http_url
to keep the same SSRF guard used elsewhere in the pipeline.
"""

import logging
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import requests

from . import net

logger = logging.getLogger("article_pipeline")

_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    # SVG is skipped: it can carry active content and isn't a "real" raster
    # image most downstream renderers expect.
}
_SKIPPED_CONTENT_TYPES = {"image/svg+xml"}

_DEFAULT_EXTENSION = ".jpg"


def _extension_from_content_type(content_type: str) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _SKIPPED_CONTENT_TYPES:
        return None
    if ct in _CONTENT_TYPE_EXTENSIONS:
        return _CONTENT_TYPE_EXTENSIONS[ct]
    return ""  # unknown/empty content-type: caller falls back to URL suffix


def _extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return ".jpg" if suffix == ".jpeg" else suffix
    return _DEFAULT_EXTENSION


def download_images(
    image_urls: List[str],
    dest_dir: Path,
    max_images: int = 8,
    max_bytes: int = 8_388_608,
    timeout: float = 15.0,
) -> List[Path]:
    """Download up to `max_images` images from `image_urls` into `dest_dir`.

    Each image is saved as ``<1-based index>.<ext>``. `dest_dir` is only
    created once the first image is about to be written, so a fully failed
    run leaves no empty directory behind. Any single-image failure (unsafe
    URL, non-2xx status, oversized payload, broken stream, etc.) is logged
    as a warning and skipped — it never aborts the batch.
    """
    dest_dir = Path(dest_dir)
    written: List[Path] = []

    for index, url in enumerate(image_urls[:max_images], start=1):
        try:
            if not net._is_safe_public_http_url(url):
                logger.warning("Skipping unsafe/non-public image URL: %s", url[:100])
                continue

            response = requests.get(url, stream=True, timeout=timeout)
            try:
                if response.status_code != 200:
                    logger.warning(
                        "Skipping image (HTTP %d): %s", response.status_code, url[:100]
                    )
                    continue

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) > max_bytes:
                            logger.warning(
                                "Skipping image (Content-Length %s > %d): %s",
                                content_length,
                                max_bytes,
                                url[:100],
                            )
                            continue
                    except ValueError:
                        pass

                ext = _extension_from_content_type(response.headers.get("Content-Type", ""))
                if ext is None:
                    logger.warning("Skipping unsupported image type (svg): %s", url[:100])
                    continue
                if not ext:
                    ext = _extension_from_url(url)

                chunks = []
                total = 0
                oversized = False
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        oversized = True
                        break
                    chunks.append(chunk)

                if oversized:
                    logger.warning(
                        "Skipping image (streamed size exceeded %d bytes): %s",
                        max_bytes,
                        url[:100],
                    )
                    continue

                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{index}{ext}"
                dest_path.write_bytes(b"".join(chunks))
                written.append(dest_path)
                logger.info("Downloaded image: %s -> %s", url[:100], dest_path)
            finally:
                response.close()
        except Exception as e:
            logger.warning("Failed to download image %s: %s", url[:100], e)
            continue

    return written
