"""The judge decides; cosine only narrows.

Measured on the real vault: `искусственный-интеллект` and `machine-learning`
score 0.84 while the true synonym pair `ии`/`искусственный-интеллект` scores
0.62. Any threshold that admits the second admits the first, so the accept/
reject logic must never be a function of cosine alone.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from propose_tag_synonyms import build_map  # noqa: E402

COUNTS = Counter({"ai": 135, "искусственный-интеллект": 95, "ии": 40, "llm": 170,
                  "llms": 20, "machine-learning": 23})
COS = {("ai", "ии"): 0.71, ("искусственный-интеллект", "machine-learning"): 0.84}


def test_a_same_concept_verdict_becomes_a_merge():
    synonyms, review = build_map(
        [{"a": "ai", "b": "ии", "same": True, "canonical": "ai"}], COUNTS, COS
    )
    assert synonyms == {"ии": "ai"}
    assert review[0]["cosine"] == 0.71


def test_a_high_cosine_pair_judged_related_is_refused():
    """The whole reason the judge exists."""
    synonyms, review = build_map(
        [{
            "a": "искусственный-интеллект",
            "b": "machine-learning",
            "same": False,
            "canonical": "искусственный-интеллект",
        }],
        COUNTS,
        COS,
    )
    assert synonyms == {}
    assert review[0]["skipped"] == "judged related, not identical"
    assert review[0]["cosine"] == 0.84, "the high score is recorded, not obeyed"


def test_chains_are_refused_not_resolved():
    synonyms, review = build_map(
        [
            {"a": "ai", "b": "ии", "same": True, "canonical": "ai"},
            # `ai` is now a canonical; it must not become a variant of anything.
            {"a": "llm", "b": "ai", "same": True, "canonical": "llm"},
        ],
        COUNTS,
        COS,
    )
    assert synonyms == {"ии": "ai"}
    assert review[1]["skipped"] == "would create a chain"


def test_a_variant_is_never_reused_as_a_canonical():
    synonyms, review = build_map(
        [
            {"a": "ai", "b": "ии", "same": True, "canonical": "ai"},
            {"a": "ии", "b": "llms", "same": True, "canonical": "ии"},
        ],
        COUNTS,
        COS,
    )
    assert synonyms == {"ии": "ai"}
    assert review[1]["skipped"] == "would create a chain"


def test_a_missing_canonical_falls_back_to_the_more_used_tag():
    synonyms, _ = build_map(
        [{"a": "llms", "b": "llm", "same": True}], COUNTS, COS
    )
    assert synonyms == {"llms": "llm"}, "merging into the rarer spelling is backwards"


def test_a_self_pair_is_dropped():
    synonyms, review = build_map(
        [{"a": "ai", "b": "ai", "same": True, "canonical": "ai"}], COUNTS, COS
    )
    assert synonyms == {}
    assert review[0]["skipped"] == "same tag"


def test_review_records_note_counts_for_every_verdict():
    _, review = build_map(
        [{"a": "ai", "b": "ии", "same": True, "canonical": "ai"}], COUNTS, COS
    )
    assert review[0]["notes"] == [40, 135], "variant first, canonical second"
