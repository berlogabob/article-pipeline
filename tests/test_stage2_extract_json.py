import pytest

from article_pipeline.stage2_summarize import extract_json_object

FULL = (
    '{"summary_ru": "s", "tags": ["a"], "author": null, '
    '"verification_notes": "v", "is_tutorial": false, "step_by_step_guidance": null}'
)


def test_plain_object():
    assert extract_json_object(FULL)["summary_ru"] == "s"


def test_object_buried_in_chatter():
    content = f"Thinking Process:\n{{'not': json}}\nHere you go:\n{FULL}\nHope this helps!"
    assert extract_json_object(content)["verification_notes"] == "v"


def test_prefers_object_with_metadata_keys():
    content = '{"random": 1} ' + FULL + ' {"also_random": 2}'
    assert "summary_ru" in extract_json_object(content)


def test_code_fenced_object():
    assert extract_json_object(f"```json\n{FULL}\n```")["tags"] == ["a"]


def test_garbage_raises():
    with pytest.raises(Exception):
        extract_json_object("no json here at all")
