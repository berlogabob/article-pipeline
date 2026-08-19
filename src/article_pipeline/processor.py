"""Per-article orchestration: ingest -> summarize -> render (markdown or typst)."""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Optional, Set, Tuple

from .config import Config
from .errors import ProcessingError
from .images import download_images
from .metadata import ArticleMetadata, normalize_tag
from .net import get_unique_path, normalize_url
from .stage1_ingest import ingest_article
from .stage2_summarize import summarize
from .stage3_render_markdown import build_content
from .stage3_render_typst import note_id, render_typst_note
from .tag_scan import VaultNote, find_related, scan_vault, vocabulary
from .youtube import YouTubePlaylistVideo

logger = logging.getLogger("article_pipeline")

_md_url_cache: Optional[Set[str]] = None

# On 2026-08-19 a backlog flush wrote hundreds of error pages into the vault
# as "articles" — 404s summarized in earnest, tagged "ошибка", and auto-related
# to each other. A page that only says the content is gone is not an article.
# ponytail: pattern list; add an LLM judge only if patterns miss real junk
_JUNK_TITLE = re.compile(
    r"(?i)(?:^\s*(?:404|403|410)\b"
    r"|page not found|page doesn.t exist|url source|access denied"
    r"|just a moment|account suspended|attention required"
    r"|страница не найдена|доступ (?:ограничен|запрещ))"
)
_ASSET_EXT = re.compile(
    r"(?i)\.(?:jpe?g|png|gif|webp|svg|ico|css|js|mp4|woff2?)$"
)
# A fetch that resolved to a site's generic shell instead of the shared page:
# login walls, JS walls, bare homepages. Exact lowercase titles.
_SHELL_TITLES = {
    "sign in",
    "log in",
    "login",
    "webmail login",
    "cpanel login",
    "google search",
    "javascript is not available.",
    "build software better, together",  # GitHub's homepage og:title
    "hugging face – the ai community building the future.",
    "verifying device",  # share.google interstitial that failed
}


def junk_reason(title: str, url: str) -> Optional[str]:
    """Why this page should be a terminal failure instead of a note, or None.

    Thin content is not checked here — ingest already enforces
    cfg.content.min_length and records it as retryable EMPTY_CONTENT.
    """
    if _JUNK_TITLE.search(title or ""):
        return f"error-page title: {title[:60]!r}"
    if (title or "").strip().lower() in _SHELL_TITLES:
        return f"site shell page: {title[:60]!r}"
    # No blanket share.google rule: the interstitial sometimes exposes the
    # real article to trafilatura, and those saves are genuine. The failed
    # interstitials surface via their own titles ("Verifying Device").
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path
    if host.endswith("hf-mirror.com") or host == "svn.code.sf.net":
        return f"raw mirror/repo host: {host}"
    if _ASSET_EXT.search(path):
        return f"raw asset URL: {path[-40:]}"
    return None


def typst_compile_error(vault: Path, note_path: Path, timeout: int = 120) -> Optional[str]:
    """Compile the note against the vault root; return stderr on failure.

    Returns None when the note compiles — or when no `typst` binary is on
    PATH, in which case the gate silently passes rather than blocking the
    pipeline on a missing dev tool.
    """
    typst = shutil.which("typst")
    if not typst:
        return None
    if not (vault / "_system" / "tylog.typ").exists():
        # A vault without the package (fresh or test vault) would fail every
        # note on the import line — that is not the note's fault.
        return None
    try:
        proc = subprocess.run(
            [typst, "compile", "--root", str(vault), str(note_path), "-"],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "typst compile timed out"
    if proc.returncode == 0:
        return None
    return proc.stderr.decode("utf-8", errors="replace")


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
    url: str,
    title: str,
    cfg: Config,
    engine,
    force: bool = False,
    force_youtube_asr: bool = False,
) -> Tuple[bool, ProcessingError]:
    ingest, err = ingest_article(
        url,
        title,
        min_length=cfg.content.min_length,
        force_youtube_asr=force_youtube_asr,
    )
    if ingest is None:
        return False, err

    final_url = ingest["expanded_url"]
    source = ingest["source"]
    # The passed-in `title` often comes from an inbox filename or link text
    # and can be generic (e.g. "codex-integration-test"); prefer the title
    # resolved from the page's own HTML/metadata when we have one.
    resolved_title = ingest["resolved_title"] or title

    if not force:
        junk = junk_reason(resolved_title, final_url)
        if junk:
            logger.info("Junk page (%s), recorded as not_found: %s", junk, final_url[:80])
            return False, ProcessingError.NOT_FOUND

    tag_vocabulary: List[str] = []
    if cfg.output_format == "typst":
        vault = Path(cfg.typst.vault_dir).expanduser()
        articles_dir = vault / cfg.typst.articles_subdir
        vault_notes = scan_vault(
            articles_dir, max_files=cfg.typst.related_articles.max_vault_files_scanned
        )
        # Offered to the model so it reuses established tags instead of coining
        # a fresh one per article — a tag nobody else uses groups nothing.
        tag_vocabulary = vocabulary(vault)
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
        vocabulary=tag_vocabulary,
    )
    if metadata is None:
        # No note: a stub that looks processed would be skipped by dedup
        # forever, so the link would be silently lost rather than retried.
        return False, ProcessingError.LLM_ERROR

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
            # Saved files are named by 1-based position in image_urls, and
            # failures are skipped — zip() would misattribute every image
            # after the first gap, so map back through the filename stem.
            for local in saved:
                orig = ingest["image_urls"][int(local.stem) - 1]
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
            title=resolved_title,
            url=final_url,
            metadata=metadata,
            body_markdown=ingest["extracted_text"],
            source=source,
            image_map=image_map,
            related=related,
            llm_provider=cfg.engine.provider or "",
            llm_model=cfg.engine.primary_model or "",
            is_youtube=ingest["is_youtube"],
            metadata_only=ingest.get("metadata_only", False),
        )
        articles_dir.mkdir(parents=True, exist_ok=True)
        out_path = get_unique_path(
            resolved_title, articles_dir, source=source, ext=".typ",
            max_chars=cfg.output.max_filename_chars,
        )
    else:
        content = build_content(
            resolved_title, final_url, metadata, ingest["extracted_text"],
            is_youtube=ingest["is_youtube"], source=source,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = get_unique_path(
            resolved_title, out_dir, source=source, ext=".md",
            max_chars=cfg.output.max_filename_chars,
        )
        if _md_url_cache is not None:
            _md_url_cache.add(ingest["normalized_url"])

    out_path.write_text(content, encoding="utf-8")

    if cfg.output_format == "typst":
        # A note the app cannot render is worse than a recorded failure —
        # 6 of 228 notes had shipped broken before this gate existed.
        compile_err = typst_compile_error(vault, out_path)
        if compile_err:
            out_path.unlink()
            logger.error(
                "Rendered note does not compile, dropped: %s\n%s",
                out_path.name,
                compile_err[:500],
            )
            return False, ProcessingError.PARSE_ERROR

    logger.info("Saved: %s", out_path.name)

    playlist_videos = ingest.get("playlist_videos") or []
    if playlist_videos:
        _save_playlist_video_notes(
            cfg=cfg,
            playlist_title=resolved_title,
            playlist_url=final_url,
            videos=playlist_videos,
            parent_metadata=metadata,
            source=source,
            out_dir=articles_dir if cfg.output_format == "typst" else out_dir,
            force=force,
            parent_note_stem=out_path.stem if cfg.output_format != "typst" else None,
            parent_note_id=nid if cfg.output_format == "typst" else None,
            vault_notes=vault_notes if cfg.output_format == "typst" else None,
        )

    return True, ProcessingError.UNKNOWN


def _playlist_video_tags(parent_tags: List[str]) -> List[str]:
    tags = list(parent_tags)
    for tag in ["youtube", "video", "курс", "урок"]:
        if tag not in tags:
            tags.append(tag)
    return tags


def _playlist_video_body(
    playlist_title: str,
    playlist_url: str,
    video: YouTubePlaylistVideo,
    *,
    parent_note_stem: Optional[str] = None,
) -> str:
    lines = [
        f"Course: {playlist_title}",
        f"Playlist: {playlist_url}",
        f"Lesson: {int(video.get('index', 0) or 0):02d}",
        f"Video ID: {video.get('video_id', '')}",
        f"Video URL: {video.get('url', '')}",
    ]
    if parent_note_stem:
        lines.insert(1, f"Course note: [[{parent_note_stem}]]")
    if video.get("published"):
        lines.append(f"Published: {video['published']}")
    if video.get("description"):
        lines.extend(["", "Description:", str(video["description"]).strip()])
    if video.get("transcript_excerpt"):
        lines.extend(["", "Transcript excerpt:", str(video["transcript_excerpt"]).strip()])
    return "\n".join(lines).strip()


def _save_playlist_video_notes(
    *,
    cfg: Config,
    playlist_title: str,
    playlist_url: str,
    videos: List[YouTubePlaylistVideo],
    parent_metadata: ArticleMetadata,
    source: str,
    out_dir: Path,
    force: bool = False,
    parent_note_stem: Optional[str] = None,
    parent_note_id: Optional[str] = None,
    vault_notes: Optional[list] = None,
) -> None:
    """Write one child note per playlist video, linked back to the parent note.

    Ported from the restored logseq-processor project's
    processor._save_playlist_video_notes: metadata for each child note is
    built directly (no extra LLM call per video), mirroring the source.
    """
    is_typst = cfg.output_format == "typst"
    tags = _playlist_video_tags(parent_metadata.tags)
    related = (
        [VaultNote(parent_note_id, playlist_title, frozenset())]
        if is_typst and parent_note_id
        else None
    )

    saved = 0
    for video in videos:
        video_url = video.get("url", "")
        if not video_url:
            continue

        if is_typst:
            vid = note_id(video_url)
            if not force and vault_notes is not None and any(n.id == vid for n in vault_notes):
                continue
        else:
            if not force and out_dir.exists() and _markdown_already_processed(video_url, out_dir):
                continue

        index = int(video.get("index", 0) or 0)
        video_title = str(video.get("title") or video.get("video_id") or "Video")
        note_title = f"{playlist_title} - {index:02d} - {video_title}"
        child_metadata = ArticleMetadata(
            summary_ru=(
                f"Урок {index:02d} из курса «{playlist_title}»: {video_title}. "
                "Заметка создана из данных YouTube-плейлиста и содержит ссылку, "
                "описание и служебные поля для дальнейшей обработки."
            ),
            tags=tags,
            author=parent_metadata.author,
            verification_notes=(
                "средняя: заметка создана из YouTube RSS-плейлиста; полный "
                "транскрипт видео не извлекался автоматически."
            ),
            is_tutorial=True,
            step_by_step_guidance=None,
        )

        if is_typst:
            body = _playlist_video_body(playlist_title, playlist_url, video)
            _, content = render_typst_note(
                title=note_title,
                url=video_url,
                metadata=child_metadata,
                body_markdown=body,
                source=source,
                related=related,
                llm_provider=cfg.engine.provider or "",
                llm_model=cfg.engine.primary_model or "",
                is_youtube=True,
            )
            out_path = get_unique_path(
                note_title, out_dir, source=source, ext=".typ",
                max_chars=cfg.output.max_filename_chars,
            )
        else:
            body = _playlist_video_body(
                playlist_title, playlist_url, video, parent_note_stem=parent_note_stem
            )
            content = build_content(
                note_title, video_url, child_metadata, body,
                is_youtube=True, source=source,
            )
            out_path = get_unique_path(
                note_title, out_dir, source=source, ext=".md",
                max_chars=cfg.output.max_filename_chars,
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        if not is_typst and _md_url_cache is not None:
            _md_url_cache.add(normalize_url(video_url))
        saved += 1
        logger.info("Saved playlist video note: %s", out_path.name)

    if saved:
        logger.info("Saved %d playlist video note(s)", saved)
