# Scripts housekeeping — design

Date: 2026-08-02. Approved by user in session.

## Goal

Close out article-pipeline's PLAN.md Phase 9: archive the pre-pipeline
projects out of Nextcloud, and add a scheduled retry pass for the failure
ledger.

## What

1. **Archive** — move to `~/Archive/scripts-pre-pipeline/` (outside
   Nextcloud, nothing deleted; harvested code also lives on GitHub):
   `old/`, `link_pro/` (712MB), `logseq-processor/`, `articles_sort/`,
   `logseq-processor.zip`, `logseq-processor 2.zip`, `cron_scheduler.py`,
   `ollama_ocr.py`, `temp_article_tagger.py`,
   `hermes_conversation_20260530_151111.json`, and the stray
   `2026-07-19-*-caveat*.txt` transcript inside `article-pipeline/`.
   Verified unreferenced by crontab and `~/Library/LaunchAgents/`.
   Frees ~835MB of Nextcloud sync; `scripts/` then holds only
   `article-pipeline/`.

2. **PLAN.md** — tick the last Phase 9 checkbox with the archive location.

3. **Scheduled retry** — new launchd agent `org.tylog.retry.plist`
   (patterned on `org.tylog.capture.plist`), weekly Sunday 10:00, running
   `uv run article-pipeline --retry-failed`. Weekly because the ledger holds
   only long-dead URLs; a collision with a capture run is at worst a
   transient llm_error the next pass fixes.

## Not doing (YAGNI)

Compressing the archive, git-initializing it, any pipeline code changes.

## Verification

`du` of archive matches ~835MB; `launchctl list` shows org.tylog.retry;
pipeline tests still pass.
