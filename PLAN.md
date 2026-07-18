# article-pipeline — implementation checklist

Merge of `logseq-processor` (ollama) + `link_pro/logseq-processor` (oMLX) into one
engine-agnostic pipeline. Each phase has a measurable check.

## Phase 0 — Harvest restored oMLX variant — DONE 2026-07-18
- [x] `~/Nextcloud/scripts/link_pro/logseq-processor` restored from Nextcloud trash
- [x] `migration_ollama_to_oxml.md` read (early transformers-based plan, superseded by
      the HTTP `omlx_client.py` — same OpenAI-compatible approach we already built)
- [x] Deltas folded in:
  - robust JSON extraction (raw_decode scan, prefers object with metadata keys) → `stage2_summarize.extract_json_object`
  - `OMLX_API_KEY` env override + `max_tokens` cap → `engine.py`
  - refined strict-JSON Russian prompt + `is_tutorial` field, guidance gated on it → `metadata.py`, both renderers
  - newest markdown contract: YAML frontmatter (Obsidian-style) → `stage3_render_markdown.py`
  - `normalize_tags` (strips #, [[ ]], dedupes) → `metadata.py`
  - multi-link markdown files, link text as title → `net.extract_links_from_markdown`, `watch.py`
  - resolved HTML titles + canonical URLs + Instagram/HuggingFace metadata fallbacks → `html_parser.py`, `stage1_ingest.py`
  - full YouTube subsystem: transcript API → yt-dlp → ASR chain, playlists/channels,
    homepage rejection, per-video child notes linked to the course note → `youtube.py`, `processor.py`
  - NOT ported (deliberately): SQLite pipeline queue + heartbeat (folder stages cover it),
    `mlx_client.py` transformers path (abandoned in the restored repo itself)

## Phase 1 — Scaffold + config wizard
- [x] `uv sync` succeeds; `uv run article-pipeline --help` works
- [x] Wizard offers ollama / omlx / claude / codex
- [x] Model list matches `curl -s :11434/v1/models | jq '.data[].id'` (http engines)
- [x] Clear error when oMLX key missing or claude/codex binary absent

## Phase 2 — Engine clients
- [x] `uv run pytest tests/test_engine_client.py` green (mocked http retry/fallback + cli subprocess)
- [x] Live smoke: ollama (full pipeline run) and claude CLI (stage2 summarize) return valid ArticleMetadata
- [ ] Live smoke against a running oMLX server (server was down during build; same OpenAI-compatible path as ollama)

## Phase 2b — Nextcloud-safe filenames
- [x] `safe_filename()` tests: emoji stripped, forbidden chars removed, <=60 chars, Cyrillic kept

## Phase 3 — Ingest
- [x] Ported `test_html_parser_backoff.py` + `test_net.py` pass
- [x] `images.py` downloads a real image (python.org logo); broken/private URLs skipped gracefully

## Phase 4 — Markdown renderer
- [x] Contract test passes with unchanged assertions
- [x] Real URL end-to-end produces valid Logseq note (wikipedia.org/wiki/Typst)

## Phase 5 — Typst renderer
- [x] `note_id()` == `md-` + sha256(url)[:16] (fixture from real vault file)
- [x] Header serialization byte-for-byte vs expected fixture
- [x] `typst compile out.typ --root ~/Nextcloud/TyLogVault` succeeds
- [x] `typst query` extracts id/title/tags + `<tylog-link>` records

## Phase 6 — Images in typst
- [x] Real downloaded image renders via `#image()` and compiles against the vault
- [x] Broken image URL -> note still written and compiles (image skipped, alt text kept)

## Phase 7 — Backlinks
- [x] Tag slug tests; `tag_scan.py` fixture-vault top-K test
- [x] `== Related` emitted only with >=1 overlap; live run produced refs to real vault note ids

## Phase 8 — Orchestration
- [x] Live run: inbox file -> `03_success` (25s incl. LLM); bad file -> `04_failed` with error suffix
- [x] `--watch` picked up a dropped file live; stale `02_processing` file auto-recovered at startup

## Phase 9 — Cutover
- [x] README matches real behavior
- [x] Repo pushed to GitHub (protection against sync loss)
- [ ] Old project folders archived (deferred until Nextcloud trash restore + Phase 0 harvest are done)
