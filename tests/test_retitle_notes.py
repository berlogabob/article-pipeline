import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from retitle_notes import (  # noqa: E402
    apply_one,
    bad_title_reason,
    new_filename,
    parse_note,
    summary_of,
)

NOTE = (
    '#import "/_system/tylog.typ" as tylog\n'
    "\n"
    "#show: tylog.note.with(\n"
    '  id: "md-abc",\n'
    '  title: "url",\n'
    '  kind: "article",\n'
    '  date: "2026-06-30",\n'
    '  tags: ("a",),\n'
    "  aliases: (),\n"
    "  project: none,\n"
    '  properties: ("status": "unread", "url": "https://youtube.com/watch?v=1", "source": "youtube.com",),\n'
    ")\n"
    "\n"
    "= url\n"
    "\n"
    "== Summary\n"
    "\n"
    "A video about growing tomatoes on a balcony.\n"
    "\n"
    "== Verification\n"
    "\n"
    "high\n"
)


def test_bad_title_detection():
    assert bad_title_reason("url", "youtube.com") == "generic"
    assert bad_title_reason("Хабр", "habr.com") == "generic"
    assert bad_title_reason("youtube.com", "youtube.com") == "title-is-domain"
    assert bad_title_reason("Reddit", "reddit.com") == "title-is-domain"
    assert bad_title_reason("Tom Dörr tom_doerr on X", "x.com") == "account-name-only"
    assert bad_title_reason("Ok", "x.com") == "too-short"
    assert bad_title_reason(None, None) == "empty"
    assert bad_title_reason("How to Build AI Applications", "x.com") is None


def test_parse_and_summary():
    note = parse_note(NOTE)
    assert note["title"] == "url"
    assert note["source"] == "youtube.com"
    assert "tomatoes" in summary_of(NOTE)
    assert "Verification" not in summary_of(NOTE)


def test_apply_rewrites_header_heading_and_renames(tmp_path):
    (tmp_path / "url.typ").write_text(NOTE, encoding="utf-8")
    apply_one(
        tmp_path,
        {
            "file": "url.typ",
            "new_title": 'Growing "balcony" tomatoes',
            "new_file": new_filename(tmp_path, "youtube.com", "Growing tomatoes"),
        },
    )
    files = list(tmp_path.glob("*.typ"))
    assert len(files) == 1
    assert files[0].name == "youtube.com - Growing tomatoes.typ"
    text = files[0].read_text(encoding="utf-8")
    assert '  title: "Growing \\"balcony\\" tomatoes",' in text
    assert "= Growing \"balcony\" tomatoes".replace('"', '"') in text
    assert '"source": "youtube.com"' in text  # properties untouched
    assert 'id: "md-abc"' in text


def test_new_filename_collision_and_length(tmp_path):
    (tmp_path / "x.com - A title.typ").write_text("x", encoding="utf-8")
    assert new_filename(tmp_path, "x.com", "A title") == "x.com - A title (2).typ"
    long = new_filename(tmp_path, "verylongdomain.example.com", "T" * 80)
    assert len(long) <= 60 + len(".typ")


def test_review_file_roundtrip(tmp_path):
    entries = [{"file": "a.typ", "new_title": "T", "new_file": "b.typ"}]
    p = tmp_path / "review.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    assert json.loads(p.read_text()) == entries
