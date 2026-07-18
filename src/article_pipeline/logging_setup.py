"""Logging setup and small print/log helpers.

Ported from the old logseq-processor project's src/common.py, with the
Config-singleton dependency removed: level/folder/console are explicit
parameters with defaults matching the old config values. Unlike the old
module, logging is NOT configured as an import-time side effect — callers
must invoke setup_logging() explicitly.
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

LOGGER_NAME = "article_pipeline"


def setup_logging(
    level: str = "INFO",
    folder: Optional[Path] = None,
    console: bool = True,
    retention_days: int = 7,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file_format = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    if folder is not None:
        folder = Path(folder).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            folder / "article_pipeline.log",
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", datefmt="[%H:%M:%S]")
        )
        logger.addHandler(console_handler)

    return logger


def _get_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_print(message: str, emoji: str = "") -> None:
    prefix = f"[{_get_timestamp()}]"
    if emoji:
        prefix += f" {emoji}"
    print(f"{prefix} {message}", flush=True)


_STAGE_COLORS = {
    "FILE": "\033[36m",  # cyan
    "QUEUE": "\033[33m",  # yellow
    "LLM": "\033[35m",  # magenta
    "SYSTEM": "\033[32m",  # green
}
_ANSI_RESET = "\033[0m"


def _supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def log_stage(stage: str, message: str) -> None:
    ts = _get_timestamp()
    stage_upper = stage.upper()
    marker = f"● {stage_upper}"
    if _supports_color():
        color = _STAGE_COLORS.get(stage_upper, "\033[37m")
        marker = f"{color}{marker}{_ANSI_RESET}"
    print(f"[{ts}] {marker} {message}", flush=True)


def log_info(message: str) -> None:
    logging.getLogger(LOGGER_NAME).info(message)


def log_error(message: str) -> None:
    logging.getLogger(LOGGER_NAME).error(message)


def log_warning(message: str) -> None:
    logging.getLogger(LOGGER_NAME).warning(message)


def log_debug(message: str) -> None:
    logging.getLogger(LOGGER_NAME).debug(message)
