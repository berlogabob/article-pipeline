"""Post-run vault upkeep: reindex so the phones inherit the desktop's work.

Indexing a TyLog vault means compiling every changed note with Typst to read
its metadata. That is minutes of CPU, and until this ran here it happened
*only* inside the app — so the desktop, which drains hundreds of articles a
day and has the fastest CPU in the fleet, contributed nothing, and each phone
recompiled the same notes for itself (measured: 14.2 min on a Huawei P30).

`tylog index` writes `_index/index.json` and publishes an index *donor* under
`_system/index/<machine>.json`, which syncs. A phone that finds a donor
matching a note's content hash skips the compile entirely.

Deliberately best-effort, following `processor.typst_compile_error`: a missing
`dart`/vault is not the drain's problem, so this logs and returns rather than
failing a run that already did its real work.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Where the Dart CLI lives. The pipeline and the app are separate repos, so
# this is a path, not an import.
_TYLOG_REPO = Path.home() / "Documents" / "GitHub" / "TypstSeq"
_TYLOG_PACKAGE = _TYLOG_REPO / "packages" / "tylog_core"


def reindex_vault(vault_dir: str, timeout: int = 1800) -> Optional[str]:
    """Rebuild the vault index and publish a donor for the other devices.

    Returns None on success or when the step was skipped; an error string when
    the CLI ran and failed. Incremental by design — no `--force`, so a routine
    post-drain run only recompiles the notes the drain actually wrote.
    """
    vault = Path(vault_dir).expanduser()
    if not (vault / "_system" / "tylog.typ").exists():
        logger.debug("reindex skipped: %s is not a TyLog vault", vault)
        return None
    dart = shutil.which("dart")
    if not dart:
        logger.debug("reindex skipped: no dart on PATH")
        return None
    if not _TYLOG_PACKAGE.exists():
        logger.debug("reindex skipped: no TyLog checkout at %s", _TYLOG_PACKAGE)
        return None
    try:
        proc = subprocess.run(
            [dart, "run", "bin/tylog.dart", "index", str(vault)],
            cwd=str(_TYLOG_PACKAGE),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("reindex timed out after %ss", timeout)
        return f"timeout after {timeout}s"
    except OSError as e:  # pragma: no cover - environment dependent
        logger.warning("reindex could not start: %s", e)
        return str(e)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        logger.warning("reindex failed: %s", detail[:200])
        return detail[:200] or f"exit {proc.returncode}"
    summary = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    if summary:
        logger.info("reindex: %s", summary[-1])
        print(summary[-1])
    return None
