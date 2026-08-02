# Vault quality — design

Date: 2026-08-02. Approved by user in session.

## Finding that shaped the design

The vault (1,772 notes) has almost no true duplicates — exactly one URL
appears twice. The `(N)`-numbered files are different tweets/videos sharing a
*title* (account-name titles, literal "url" titles). The real problems are
~100 notes with non-descriptive titles, and a tag vocabulary of 3,812 unique
tags of which 80% are singletons, with case and language variants.

## Part 1 — retitle pass (`scripts/retitle_notes.py`, new)

Detection — a title is bad when any of:
- literal junk: "url", or a known generic shell ("Хабр", "Google Search",
  "Build software better, together", "JavaScript is not available.")
- it equals the note's source domain (or the domain minus TLD)
- account-name-only social titles: matches `.* on (X|Threads)$`
- ≤ 4 characters

Proposal — one sonnet call per hit (engine from the pipeline's own
config/engine client) over the note's Summary section (fallback: first 800
chars of body). Ask for a specific ≤55-char title in the note's language.
Output: a JSON review file (path, old/new title, new filename, reason).
Nothing changes without `--apply` — same contract as the tag scripts.

Apply — rewrite the `title:` header property (`_typst_string`) and the `= `
body heading (`escape_markup`), rename via `safe_filename` (≤60 chars,
`(N)` on collision). `id:` and asset folders untouched so TypstSeq links
survive. The review file doubles as the undo log.

## Part 2 & 3 — tags (existing scripts)

Run `propose_tag_synonyms.py` (ollama bge-m3 + LLM judge → merge map,
review, `--apply`; the map is read by the scanner, notes are not rewritten),
then `remap_rare_tags.py` (adds established parent tags to unclustered
notes, review, `--apply`).

## Also

Delete the single true URL duplicate, keeping the newer note.

## Testing

Unit tests for detection and for apply (header/heading rewrite, rename,
collision). Smoke: apply on a 5-note copy before the real vault.

## Not doing (YAGNI)

Refetching sources, retitling mediocre-but-specific titles, TypstSeq-side
changes, re-tagging bodies.
