import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from remap_rare_tags import (  # noqa: E402
    add_header_tags,
    additions_for,
    implied_tag,
    normalize,
)

VOCAB = {"google", "openai", "ai", "python", "ai-development", "rust"}


def test_a_specific_tag_gains_the_general_one_it_contains():
    assert implied_tag("google-slides", VOCAB) == "google"
    assert implied_tag("openai-api", VOCAB) == "openai"


def test_it_never_invents_specificity():
    """`development` -> `ai-development` would put a non-AI article in the AI
    cluster. The mapping only ever goes specific -> general."""
    assert implied_tag("development", VOCAB) is None
    assert implied_tag("slides", VOCAB) is None


def test_matching_is_segment_wise_not_substring():
    """Substring matching maps rust->trust and ai->air-quality."""
    assert implied_tag("trust-issues", VOCAB) is None
    assert implied_tag("air-quality", VOCAB) is None
    assert implied_tag("rust-embedded", VOCAB) == "rust"


def test_the_most_specific_established_tag_wins():
    assert implied_tag("ai-development-tools", VOCAB) == "ai-development"


def test_an_already_clustering_tag_needs_nothing():
    assert implied_tag("python", VOCAB) is None


def test_an_article_that_already_clusters_is_left_alone():
    note = {"tags": ["python", "some-rare-thing"]}
    assert additions_for(note, VOCAB) == []


def test_an_unclustered_article_gets_its_implied_tags():
    note = {"tags": ["google-slides", "openai-api", "totally-novel"]}
    assert additions_for(note, VOCAB) == ["google", "openai"]


def test_normalize_matches_the_dart_side():
    assert normalize("  AI Tools ") == "ai-tools"
    assert normalize("deep_learning") == "deep-learning"


HEADER = (
    '#import "/_system/tylog.typ" as tylog\n\n'
    "#show: tylog.note.with(\n"
    '  id: "a",\n'
    '  title: "A",\n'
    "  tags: {tags},\n"
    "  aliases: (),\n"
    ")\n\n= A\n\ntags:: #google slides\n"
)


def test_tags_are_added_to_an_empty_header_list():
    out = add_header_tags(HEADER.replace("{tags}", "()"), ["google"])
    assert '  tags: ("google",),\n' in out
    # The body line is untouched — it is the source of the original tags.
    assert "tags:: #google slides" in out


def test_existing_header_tags_are_preserved_and_not_duplicated():
    src = HEADER.replace("{tags}", '("kept", "google")')
    out = add_header_tags(src, ["google", "openai"])
    assert '  tags: ("kept", "google", "openai",),\n' in out


def test_adding_nothing_leaves_the_source_byte_identical():
    src = HEADER.replace("{tags}", '("kept",)')
    assert add_header_tags(src, []) == src


def test_a_note_with_no_header_tags_field_is_untouched():
    src = "= Just a body\n\nno header here\n"
    assert add_header_tags(src, ["google"]) == src
