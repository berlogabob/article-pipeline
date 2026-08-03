import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_digest import (  # noqa: E402
    OTHER,
    articles_for_week,
    group_by_tag,
    render_digest,
    week_bounds,
)


def _note(nid, title, processed, tags=(), kind="article"):
    return {
        "id": nid,
        "title": title,
        "kind": kind,
        "tags": list(tags),
        "properties": {"processed": processed, "source": "a.example"},
    }


INDEX = {
    "notes": [
        _note("md-1", "In week", "2026-07-29", ["LLM"]),
        _note("md-2", "Also in week", "2026-08-02", ["gardening"]),
        _note("md-3", "Before week", "2026-07-26"),
        _note("md-4", "Not an article", "2026-07-29", kind="daily"),
        _note("md-5", "No processed date", ""),
    ]
}


def test_week_bounds_and_selection():
    assert week_bounds("2026-W31") == (
        __import__("datetime").date(2026, 7, 27),
        __import__("datetime").date(2026, 8, 2),
    )
    picked = articles_for_week(INDEX, "2026-W31")
    assert [n["id"] for n in picked] == ["md-1", "md-2"]


def test_grouping_prefers_established_tags():
    groups = group_by_tag(articles_for_week(INDEX, "2026-W31"), vocab=["LLM"])
    assert [a["id"] for a in groups["LLM"]] == ["md-1"]
    assert [a["id"] for a in groups[OTHER]] == ["md-2"]


def test_render_is_valid_tylog_and_clickable():
    groups = group_by_tag(articles_for_week(INDEX, "2026-W31"), vocab=["LLM"])
    out = render_digest("2026-W31", groups)
    assert '  id: "digest-2026-w31",' in out
    assert '  kind: "note",' in out
    assert '"articles": "2"' in out
    assert '#tylog.ref-note("md-1")[In week] — a.example' in out
    assert out.index("== LLM") < out.index("== " + OTHER.replace("/", "\\/"))
