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
uv run article-pipeline --retry-failed  # re-run the retryable URLs in failures.jsonl
```

The wizard asks:
1. **Engine** — `ollama`, `omlx` (both local HTTP), `claude` or `codex` (CLI).
2. **Primary + fallback model** — scanned live from the engine.
3. **Output format** — `markdown` (Logseq) or `typst` (TyLog Format v1).

Answers are saved to `config.yaml`; later runs are silent. No API keys are
stored — the oMLX key is read from `~/.omlx/settings.json` each run.

## Data stages

`01_inbox/` → `02_processing/` → `03_success/` (originals) or `04_failed/`
(with an `_error_<reason>_<ts>` suffix). A file stuck in `02_processing/` after
a crash can simply be moved back to `01_inbox/`.

A file counts as a success if *any* of its links worked, so the folders alone
cannot tell you about the ones that didn't. `failures.jsonl` records one line
per failed URL:

```json
{"ts":"…","source_file":"Source X.md","url":"…","error":"blocked","retryable":false}
```

`blocked` (401/403/rate-limited) and `not_found` (404/410) are terminal —
`--retry-failed` skips them and re-runs only what might behave differently
next time, rewriting the ledger with whatever still fails.

Pages with no extractable body but usable Open Graph tags (X, Threads,
YouTube without captions) become short notes marked
`extraction: "metadata-only"`, so they can be found and reprocessed if the
source ever becomes readable.

See `PLAN.md` for the implementation checklist.

## TyLog article worker

The independent worker drains `~/Nextcloud/TyLogVault/_system/jobs/articles/*.json`.
A producer writes `{ "url": "https://example.com/article", "title": "optional",
"shared_at": "2026-10-05T12:00:00Z", "status": "queued" }`. Jobs transition through
`processing` to `done` with a vault-relative `note_path`, or `error` with `reason`.
Other producer fields are preserved. Set an error job back to `queued` to retry.
Interrupted `processing` jobs resume on the next run; existing article IDs prevent
another note being written. Run workers on one Mac only: the lock coordinates
local processes, not machines through Nextcloud.

```bash
uv sync
uv run tylog-worker --once
uv run tylog-worker --watch
uv run tylog-worker --once --vault /path/to/test-vault
uv run tylog-worker --once --provider configured  # use config.yaml's engine
```

By default the worker uses Studio at `http://desktop-vdsrh2e:8888/v1`, first
`unsloth/gemma-4-26B-A4B-it-qat-GGUF`, then `ollama/qwen3.6:27b` through the same
endpoint. The key is read at request time from `~/.pi/agent/models.json`, field
`providers.studio.apiKey`; credentials and HTTP response bodies are excluded from
Studio error messages. No key is saved in pipeline config or the launchd plist.
The worker uses the existing ingest, summary, Typst note, image, related-link,
compile-check and vault-index stages, with the configured vault/output settings.
It always outputs Typst. Existing `article-pipeline` and Nextcloud Notes inbox
commands keep using their existing configuration.

To use Studio with the Notes route too, set `engine.provider: studio` in
`config.yaml` and set `primary_model` / `fallback_model` to the models above;
`engine.base_url` optionally overrides the Studio endpoint.

Generate a launchd agent (the generator writes the plist; it does not load it):

```bash
uv run tylog-worker --launchd-plist ~/Library/LaunchAgents/org.tylog.worker.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.tylog.worker.plist
```

The agent runs once at load, on jobs-directory changes, and every 60 seconds
(to catch sync changes missed by directory notifications). It uses an absolute
`uv` path and the repo working directory. To stop it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/org.tylog.worker.plist
```
