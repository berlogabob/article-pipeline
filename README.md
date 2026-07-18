# article-pipeline

One pipeline for turning saved article URLs into PKMS notes, merging the former
`logseq-processor` (Ollama) and its oMLX fork.

Drop `.md` files containing URLs (or `tabs*.html` exports) into `01_inbox/`, run
the pipeline, and get either Logseq markdown notes or TyLog Typst articles
(for the TypstSeq app vault at `~/Nextcloud/TyLogVault/articles/`).

## Usage

```bash
uv sync
uv run article-pipeline              # first run: interactive setup wizard
uv run article-pipeline --watch      # keep watching 01_inbox/
uv run article-pipeline --reconfigure  # re-ask engine/models/format
uv run article-pipeline --force      # reprocess even if seen before
```

The wizard asks:
1. **Engine** — `ollama`, `omlx` (both local HTTP), `claude` or `codex` (CLI).
2. **Primary + fallback model** — scanned live from the engine.
3. **Output format** — `markdown` (Logseq) or `typst` (TyLog Format v1).

Answers are saved to `config.yaml`; later runs are silent. No API keys are
stored — the oMLX key is read from `~/.omlx/settings.json` each run.

## Data stages

`01_inbox/` → `02_processing/` → `03_success/` (originals) or `04_failed/`
(with error suffix). A file stuck in `02_processing/` after a crash can simply
be moved back to `01_inbox/`.

See `PLAN.md` for the implementation checklist.
