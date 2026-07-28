#!/bin/bash
# Copy newly-saved links from the notes folder into the pipeline inbox, then
# process them once.
#
# "New" means: no file of the same stem has already landed in 03_success or
# 04_failed. The folder stages are the only state — there is no separate
# database to drift out of sync with them. Validated against the real vault,
# where 185 of 187 files matched an existing counterpart this way.
#
# Only the notes folder's root is read. Its subfolders (originals/, articles/,
# vaults/, …) hold different things and are deliberately left alone.
set -uo pipefail

NOTES="${TYLOG_NOTES_DIR:-$HOME/Nextcloud/Notes}"
PIPELINE="${TYLOG_PIPELINE_DIR:-$HOME/Nextcloud/scripts/article-pipeline}"
INBOX="$PIPELINE/01_inbox"

cd "$PIPELINE" || { echo "no pipeline at $PIPELINE" >&2; exit 1; }
[ -d "$NOTES" ] || { echo "no notes folder at $NOTES" >&2; exit 1; }
mkdir -p "$INBOX"

# A processed file may have been renamed on the way: `_1` for a name clash, or
# `_error_<reason>_<epoch>` on failure. Strip both to recover the stem.
processed="$(
  ls "$PIPELINE/03_success" "$PIPELINE/04_failed" 2>/dev/null |
    sed -E 's/(_error_[a-z_]+_[0-9]+)?(_[0-9]+)?\.md$//' | sort -u
)"

staged=0
for file in "$NOTES"/*.md; do
  [ -e "$file" ] || continue
  stem="$(basename "$file" .md)"
  grep -qxF "$stem" <<<"$processed" && continue
  # Already waiting from an earlier run that has not drained yet.
  [ -e "$INBOX/$(basename "$file")" ] && continue
  cp "$file" "$INBOX/" && staged=$((staged + 1))
done

if [ "$staged" -eq 0 ]; then
  echo "$(date '+%F %T') nothing new"
  exit 0
fi

echo "$(date '+%F %T') staged $staged; processing"
# One pass, not --watch: launchd re-runs this whenever the folder changes, so a
# long-lived watcher would just be a second, redundant trigger.
exec uv run article-pipeline
