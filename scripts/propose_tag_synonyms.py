"""Propose tag merges so one concept stops being three clusters.

The vault is bilingual, so the same concept is often spelled several ways —
`ai` / `искусственный-интеллект` / `ии` were 270 notes in three partial
clusters. Tags are compared by exact string everywhere downstream, so each
spelling clusters separately.

Two stages, because neither alone is sufficient (both measured on this vault):

1. **Embeddings narrow.** bge-m3 puts all languages in one space, so cosine
   finds cross-language candidates without translation. But it cannot tell
   "same concept" from "closely related": `искусственный-интеллект` and
   `machine-learning` score 0.84, *above* true synonyms like
   `ии`/`искусственный-интеллект` at 0.62. Cosine is a recall tool here, not a
   decision.
2. **The LLM judges.** Same-or-merely-related is a question a language model
   answers well. It sees the note counts too, so it can pick the canonical.

Co-occurrence was tried as a veto and dropped: cross-language pairs co-occur
~0% whether or not they are synonyms, because Russian-tagged and English-tagged
articles are largely disjoint sets. It discriminates only within a language,
which is the case that needed no help.

Output is a review file. Nothing is applied without --apply, and even then the
result is a map the scanner reads — no note is ever rewritten.

    uv run python scripts/propose_tag_synonyms.py ~/Nextcloud/TyLogVault
    uv run python scripts/propose_tag_synonyms.py ~/Nextcloud/TyLogVault --apply
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

EMBED_MODEL = "bge-m3"
OLLAMA = "http://127.0.0.1:11434"
# Deliberately loose: this is the recall stage. Precision comes from the judge,
# so the only cost of a low threshold is judge calls — but the cost of a high
# one is a merge silently never considered. Calibrated against a known-true
# pair: `ai`/`искусственный-интеллект` scores 0.667, the largest single
# fragmentation in the vault, and 0.70 dropped it before it was ever judged.
CANDIDATE_COSINE = 0.66
# Judging every pair of 5000 tags is impossible; judging the tags that actually
# cluster something is enough. A tag below this never forms a cluster anyway.
MIN_NOTES = 5
BATCH = 64
# Small enough that one reply is a short generation; the CLI times out at 120s.
JUDGE_BATCH = 30


def embed(texts: Sequence[str], model: str = EMBED_MODEL) -> List[List[float]]:
    out: List[List[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = list(texts[i : i + BATCH])
        body = json.dumps({"model": model, "input": chunk}).encode()
        request = urllib.request.Request(
            f"{OLLAMA}/api/embed", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            out.extend(json.load(response)["embeddings"])
    return out


def _unit(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def tag_counts(vault: Path, min_notes: int = MIN_NOTES) -> Counter:
    notes = json.loads((vault / "_index" / "index.json").read_text(encoding="utf-8"))["notes"]
    counts: Counter[str] = Counter()
    for note in notes:
        for tag in note.get("tags") or []:
            tag = str(tag).strip().lower()
            if tag:
                counts[tag] += 1
    return Counter({t: n for t, n in counts.items() if n >= min_notes})


def candidates(
    counts: Counter,
    threshold: float = CANDIDATE_COSINE,
    cache: Optional[Path] = None,
) -> List[Tuple[str, str, float]]:
    """Pairs worth asking about, most-similar first.

    Embeddings are cached by tag, so re-running after a threshold change or a
    failed judge pass costs nothing.
    """
    tags = sorted(counts, key=lambda t: -counts[t])
    known: dict = {}
    if cache and cache.exists():
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
    missing = [t for t in tags if t not in known]
    if missing:
        print(f"embedding {len(missing)} tags ({len(tags) - len(missing)} cached)...", flush=True)
        for tag, vector in zip(missing, embed(missing)):
            known[tag] = _unit(vector)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
    vectors = [known[t] for t in tags]
    pairs: List[Tuple[str, str, float]] = []
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            cosine = sum(a * b for a, b in zip(vectors[i], vectors[j]))
            if cosine >= threshold:
                # More-used tag first: it is the natural canonical, and the
                # judge is told so.
                a, b = tags[i], tags[j]
                if counts[b] > counts[a]:
                    a, b = b, a
                pairs.append((a, b, cosine))
    pairs.sort(key=lambda p: -p[2])
    return pairs


JUDGE_PROMPT = """Ты — библиотекарь, наводящий порядок в тегах личной библиотеки.

Для каждой пары реши: это ДВА НАПИСАНИЯ ОДНОГО И ТОГО ЖЕ понятия, или два
разных (пусть и близких) понятия?

Одно понятие — это перевод, аббревиатура, синоним или форма числа:
  "ии" и "ai" — одно понятие
  "llms" и "llm" — одно понятие
  "машинное-обучение" и "machine-learning" — одно понятие

Разные понятия — даже когда они тесно связаны:
  "искусственный-интеллект" и "machine-learning" — РАЗНЫЕ
  "llm" и "rag" — РАЗНЫЕ
  "python" и "программирование" — РАЗНЫЕ

Верни только JSON-массив, по объекту на пару, в том же порядке:
[{{"a": "...", "b": "...", "same": true, "canonical": "..."}}]

canonical — то написание, которое стоит оставить: обычно то, у которого больше
заметок, но выбери более понятное, если разница невелика.

Пары (в скобках — сколько заметок несут тег):
{pairs}
"""


def judge(
    pairs: List[Tuple[str, str, float]],
    counts: Counter,
    engine,
    model: str,
    batch: int = JUDGE_BATCH,
) -> List[dict]:
    """Ask the model about every candidate, a batch at a time.

    Batched because the whole candidate list in one prompt is a long generation
    that trips the CLI's timeout, and because a failed batch then costs one
    slice rather than the entire run.
    """
    verdicts: List[dict] = []
    for start in range(0, len(pairs), batch):
        chunk = pairs[start : start + batch]
        listing = "\n".join(
            f'- "{a}" ({counts[a]}) / "{b}" ({counts[b]})' for a, b, _ in chunk
        )
        print(f"  judging {start + 1}-{start + len(chunk)} of {len(pairs)}...", flush=True)
        try:
            raw = engine.chat_json(JUDGE_PROMPT.format(pairs=listing), model, 0.0)
            begin, end = raw.find("["), raw.rfind("]")
            if begin < 0 or end < 0:
                raise ValueError(f"no JSON array in reply: {raw[:120]}")
            verdicts.extend(json.loads(raw[begin : end + 1]))
        except Exception as e:  # noqa: BLE001 - a bad batch must not lose the good ones
            print(f"    batch failed ({type(e).__name__}: {e}); skipped", flush=True)
    return verdicts


def build_map(
    verdicts: List[dict], counts: Counter, cosines: Dict[Tuple[str, str], float]
) -> Tuple[Dict[str, str], List[dict]]:
    """Accepted merges, plus every verdict annotated for review.

    Chains are refused rather than resolved: if `a -> b` is already accepted,
    `b` cannot later become a variant of something else. Chained merges are how
    a synonym set drifts into a neighbouring concept.
    """
    synonyms: Dict[str, str] = {}
    review: List[dict] = []
    for verdict in verdicts:
        a, b = str(verdict.get("a", "")), str(verdict.get("b", ""))
        same = bool(verdict.get("same"))
        canonical = str(verdict.get("canonical") or (a if counts[a] >= counts[b] else b))
        variant = b if canonical == a else a
        row = {
            "variant": variant,
            "canonical": canonical,
            "same": same,
            "cosine": round(cosines.get((a, b), cosines.get((b, a), 0.0)), 3),
            "notes": [counts.get(variant, 0), counts.get(canonical, 0)],
        }
        if not same:
            row["skipped"] = "judged related, not identical"
        elif (
            variant in synonyms  # already merged into something else
            or variant in synonyms.values()  # it is a canonical; merging it chains
            or canonical in synonyms  # canonical is itself a variant
        ):
            row["skipped"] = "would create a chain"
        elif variant == canonical:
            row["skipped"] = "same tag"
        else:
            synonyms[variant] = canonical
        review.append(row)
    return synonyms, review


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path)
    parser.add_argument("--apply", action="store_true", help="write tag-synonyms.json")
    parser.add_argument("--threshold", type=float, default=CANDIDATE_COSINE)
    parser.add_argument("--min-notes", type=int, default=MIN_NOTES)
    args = parser.parse_args(argv)

    vault = args.vault.expanduser()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from article_pipeline.config import load_config
    from article_pipeline.engine import make_engine

    cfg = load_config(Path(__file__).resolve().parents[1])
    if cfg is None:
        print("no config.yaml; run the pipeline once first", file=sys.stderr)
        return 1

    counts = tag_counts(vault, args.min_notes)
    print(f"{len(counts)} tags on >={args.min_notes} notes")

    cache = vault / "_index" / "tag-embeddings.json"
    pairs = candidates(counts, args.threshold, cache)
    print(f"{len(pairs)} candidate pairs at cosine >= {args.threshold}")
    if not pairs:
        return 0

    cosines = {(a, b): c for a, b, c in pairs}
    verdicts = judge(pairs, counts, make_engine(cfg), cfg.engine.primary_model or "")
    synonyms, review = build_map(verdicts, counts, cosines)

    report = vault / "_index" / "tag-synonyms.review.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(synonyms)} merges accepted of {len(review)} judged")
    print(f"review written to {report}")
    for row in review:
        if not row.get("skipped"):
            print(f"  {row['variant']} ({row['notes'][0]}) -> {row['canonical']} ({row['notes'][1]})  cos {row['cosine']}")

    if args.apply:
        target = vault / "_system" / "tag-synonyms.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"schema": 1, "synonyms": synonyms}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {target} — rebuild the index (force) to apply")
    else:
        print("\nre-run with --apply to write _system/tag-synonyms.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
