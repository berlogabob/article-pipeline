"""Reusing the library's tags is what makes articles clusterable.

Left to itself the model coins a fresh tag per article: of 4345 tags in the
real vault, 3617 were used exactly once, so half the articles shared no tag
with 5+ notes and could not be grouped at all.
"""

import json

from article_pipeline.metadata import build_prompt, build_vocabulary_section, normalize_tags
from article_pipeline.tag_scan import vocabulary


def _vault(tmp_path, notes):
    index = tmp_path / "_index"
    index.mkdir(parents=True)
    (index / "index.json").write_text(
        json.dumps({"version": 6, "notes": notes}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_vocabulary_keeps_only_tags_that_can_cluster(tmp_path):
    notes = [{"path": f"articles/{i}.typ", "tags": ["python"]} for i in range(6)]
    notes += [{"path": f"articles/x{i}.typ", "tags": ["rare"]} for i in range(2)]
    vault = _vault(tmp_path, notes)

    vocab = vocabulary(vault)

    assert "python" in vocab
    assert "rare" not in vocab, "a tag below the threshold groups nothing"


def test_vocabulary_is_ordered_by_use_and_capped(tmp_path):
    notes = []
    for i in range(20):
        notes.append({"path": f"a{i}.typ", "tags": ["common"]})
    for i in range(6):
        notes.append({"path": f"b{i}.typ", "tags": ["less-common"]})
    vault = _vault(tmp_path, notes)

    assert vocabulary(vault) == ["common", "less-common"]
    assert vocabulary(vault, limit=1) == ["common"]


def test_vocabulary_reads_the_index_not_the_headers(tmp_path):
    """Imported articles keep `tags: ()` and their real tags on a body
    `tags::` line, so header scanning would miss them entirely."""
    notes = [{"path": f"a{i}.typ", "tags": ["из-индекса"]} for i in range(5)]
    vault = _vault(tmp_path, notes)
    (vault / "articles").mkdir()
    (vault / "articles" / "a0.typ").write_text(
        '#show: tylog.note.with(id: "a", title: "A", tags: ())\n', encoding="utf-8"
    )

    assert vocabulary(vault) == ["из-индекса"]


def test_a_missing_or_corrupt_index_yields_no_vocabulary(tmp_path):
    assert vocabulary(tmp_path) == []
    (tmp_path / "_index").mkdir()
    (tmp_path / "_index" / "index.json").write_text("not json {", encoding="utf-8")
    assert vocabulary(tmp_path) == []


def test_the_prompt_carries_the_vocabulary_and_the_instruction():
    section = build_vocabulary_section(["ai", "python"])
    assert "ai, python" in section
    assert "Новый тег" in section, "the model must be told when NOT to reuse"

    prompt = build_prompt("body text", vocabulary=["ai", "python"])
    assert "ai, python" in prompt
    assert "Текст:" in prompt


def test_no_vocabulary_leaves_the_prompt_as_it_was():
    assert build_vocabulary_section([]) == ""
    assert build_vocabulary_section(None) == ""
    assert "Словарь" not in build_prompt("body text")


def test_normalize_tags_matches_the_app_casing():
    # scanner.dart's _normalizeTag: trim, lowercase, whitespace/underscore -> '-'
    assert normalize_tags(["AI", "ai", "Machine Learning", "deep_learning"]) == [
        "ai",
        "machine-learning",
        "deep-learning",
    ]
