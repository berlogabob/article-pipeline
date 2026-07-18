"""Per-article orchestration: ingest -> summarize -> render (markdown or typst)."""

import logging
from pathlib import Path
from typing import Optional, Set, Tuple

from .config import Config
from .errors import ProcessingError
from .images import download_images
from .metadata import normalize_tag
from .net import get_unique_path, normalize_url
from .stage1_ingest import ingest_article
from .stage2_summarize import summarize
from .stage3_render_markdown import build_content
from .stage3_render_typst import note_id, render_typst_note
from .tag_scan import find_related, scan_vault

logger = logging.getLogger("article_pipeline")

_md_url_cache: Optional[Set[str]] = None


def _markdown_already_processed(url: str, folder: Path) -> bool:
    global _md_url_cache
    if _md_url_cache is None:
        _md_url_cache = set()
        for file in folder.rglob("*.md"):
            try:
                for line in file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("url:: "):
                        _md_url_cache.add(normalize_url(line[6:].strip()))
            except OSError:
                continue
    return normalize_url(url) in _md_url_cache


def process_article(
    url: str, title: str, cfg: Config, engine, force: bool = False
) -> Tuple[bool, ProcessingError]:
    ingest, err = ingest_article(url, title, min_length=cfg.content.min_length)
    if ingest is None:
        return False, err

    final_url = ingest["expanded_url"]
    source = ingest["source"]

    if cfg.output_format == "typst":
        vault = Path(cfg.typst.vault_dir).expanduser()
        articles_dir = vault / cfg.typst.articles_subdir
        vault_notes = scan_vault(
            articles_dir, max_files=cfg.typst.related_articles.max_vault_files_scanned
        )
        nid = note_id(final_url)
        if not force and any(n.id == nid for n in vault_notes):
            logger.info("Already in vault (id %s): %s", nid, final_url[:80])
            return True, ProcessingError.UNKNOWN
    else:
        out_dir = Path(cfg.markdown.out_dir).expanduser()
        if not force and out_dir.exists() and _markdown_already_processed(final_url, out_dir):
            logger.info("Already processed: %s", final_url[:80])
            return True, ProcessingError.UNKNOWN

    metadata = summarize(
        engine,
        ingest["extracted_text"],
        primary_model=cfg.engine.primary_model or "",
        fallback_model=cfg.engine.fallback_model,
        temperature_attempts=cfg.llm.temperature_attempts,
        is_youtube=ingest["is_youtube"],
        max_prompt_chars=cfg.content.max_length,
        url=final_url,
    )

    if cfg.output_format == "typst":
        image_map = {}
        if cfg.typst.download_images and ingest["image_urls"]:
            assets_dir = vault / cfg.typst.assets_subdir / nid
            saved = download_images(
                ingest["image_urls"],
                assets_dir,
                max_images=cfg.typst.max_images_per_article,
                max_bytes=cfg.typst.max_image_bytes,
            )
            for orig, local in zip(ingest["image_urls"], saved):
                image_map[orig] = f"/{cfg.typst.assets_subdir}/{nid}/{local.name}"

        related = []
        if cfg.typst.related_articles.enabled:
            related = find_related(
                [normalize_tag(t) for t in metadata.tags],
                vault_notes,
                exclude_id=nid,
                max_links=cfg.typst.related_articles.max_links,
            )

        _, content = render_typst_note(
            title=title,
            url=final_url,
            metadata=metadata,
            body_markdown=ingest["extracted_text"],
            source=source,
            image_map=image_map,
            related=related,
            llm_provider=cfg.engine.provider or "",
            llm_model=cfg.engine.primary_model or "",
            is_youtube=ingest["is_youtube"],
        )
        articles_dir.mkdir(parents=True, exist_ok=True)
        out_path = get_unique_path(
            title, articles_dir, source=source, ext=".typ",
            max_chars=cfg.output.max_filename_chars,
        )
    else:
        content = build_content(
            title, final_url, metadata, ingest["extracted_text"],
            is_youtube=ingest["is_youtube"], max_len=cfg.content.max_length,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = get_unique_path(
            title, out_dir, source=source, ext=".md",
            max_chars=cfg.output.max_filename_chars,
        )
        if _md_url_cache is not None:
            _md_url_cache.add(ingest["normalized_url"])

    out_path.write_text(content, encoding="utf-8")
    logger.info("Saved: %s", out_path.name)
    return True, ProcessingError.UNKNOWN
