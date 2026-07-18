"""Config schema + load/save, mirroring config.example.yaml."""

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel

CONFIG_EXAMPLE_FILENAME = "config.example.yaml"
CONFIG_FILENAME = "config.yaml"


class EngineConfig(BaseModel):
    provider: Optional[Literal["ollama", "omlx", "claude", "codex"]] = None
    base_url: Optional[str] = None
    primary_model: Optional[str] = None
    fallback_model: Optional[str] = None


class RateLimitConfig(BaseModel):
    delay_per_domain: float = 2
    delay_global: float = 1


class ContentConfig(BaseModel):
    min_length: int = 100
    max_length: int = 8000


class HttpConfig(BaseModel):
    retry_429_count: int = 3
    retry_429_backoff_seconds: float = 1.0


class LlmConfig(BaseModel):
    timeout_seconds: int = 120
    max_parallel_jobs: int = 1
    temperature_attempts: list[float] = [0.05, 0.25, 0.50]


class OutputConfig(BaseModel):
    max_filename_chars: int = 60


class FoldersConfig(BaseModel):
    inbox: str = "01_inbox"
    processing: str = "02_processing"
    success: str = "03_success"
    failed: str = "04_failed"


class MarkdownConfig(BaseModel):
    out_dir: str = "~/Nextcloud/Notes/articles"


class RelatedArticlesConfig(BaseModel):
    enabled: bool = True
    max_links: int = 5
    max_vault_files_scanned: int = 5000


class TypstConfig(BaseModel):
    vault_dir: str = "~/Nextcloud/TyLogVault"
    articles_subdir: str = "articles"
    assets_subdir: str = "assets/articles"
    download_images: bool = True
    max_images_per_article: int = 8
    max_image_bytes: int = 8388608
    related_articles: RelatedArticlesConfig = RelatedArticlesConfig()


class LoggingConfig(BaseModel):
    level: str = "INFO"
    folder: str = "~/.article-pipeline/logs"
    retention_days: int = 7
    console: bool = True


class Config(BaseModel):
    engine: EngineConfig = EngineConfig()
    output_format: Optional[Literal["markdown", "typst"]] = None
    max_retries: int = 3
    warmup_llm: bool = True
    rate_limit: RateLimitConfig = RateLimitConfig()
    content: ContentConfig = ContentConfig()
    http: HttpConfig = HttpConfig()
    llm: LlmConfig = LlmConfig()
    output: OutputConfig = OutputConfig()
    folders: FoldersConfig = FoldersConfig()
    markdown: MarkdownConfig = MarkdownConfig()
    typst: TypstConfig = TypstConfig()
    logging: LoggingConfig = LoggingConfig()

    def folders_abs(self, root: Path) -> dict[str, Path]:
        """Absolute paths for the data-stage folders, relative to project root."""
        return {
            "inbox": root / self.folders.inbox,
            "processing": root / self.folders.processing,
            "success": root / self.folders.success,
            "failed": root / self.folders.failed,
        }


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay `overlay` onto `base`, returning a new dict."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(root: Path) -> Optional[Config]:
    """Load config.example.yaml as defaults, overlay config.yaml if present.

    Returns None if config.yaml is missing, or if engine.provider is still
    null (both cases mean the wizard needs to run).
    """
    example_path = root / CONFIG_EXAMPLE_FILENAME
    config_path = root / CONFIG_FILENAME

    if not config_path.exists():
        return None

    defaults: dict[str, Any] = {}
    if example_path.exists():
        defaults = yaml.safe_load(example_path.read_text()) or {}

    overlay = yaml.safe_load(config_path.read_text()) or {}
    merged = _deep_merge(defaults, overlay)

    cfg = Config(**merged)
    if cfg.engine.provider is None:
        return None
    return cfg


def save_config(cfg: Config, root: Path) -> Path:
    """Write config.yaml at the project root. Returns the path written."""
    config_path = root / CONFIG_FILENAME
    data = cfg.model_dump()
    config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return config_path
