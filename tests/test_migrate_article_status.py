import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migrate_article_status import later_stage, migrate_source, stage  # noqa: E402

HEADER = (
    '#import "/_system/tylog.typ" as tylog\n'
    "\n"
    "#show: tylog.note.with(\n"
    '  id: "md-x",\n'
    '  title: "T",\n'
    '  kind: "article",\n'
    '  date: "2026-06-30",\n'
    '  tags: ("ai",),\n'
    "  aliases: (),\n"
    "  project: none,\n"
    "  properties: ({props},),\n"
    ")\n"
    "\n"
    "= T\n"
)


def note(props: str) -> str:
    return HEADER.replace("{props}", props)


def props_of(source: str) -> str:
    for line in source.splitlines():
        if line.startswith("  properties: ("):
            return line
    raise AssertionError("no properties line")


def test_stage_matches_the_dart_vocabulary():
    assert stage("processed") == "unread"
    assert stage("") == "unread"
    assert stage(None) == "unread"
    assert stage("summarized") == "extracted"
    assert stage("skimmed") == "skimmed"
    assert stage("cited") == "cited"
    assert stage("anything-else") == "read"


def test_later_stage_never_walks_backwards():
    assert later_stage("processed", "read") == "read"
    assert later_stage("read", "unread") == "read"
    assert later_stage("processed", "unread") == "unread"
    assert later_stage("cited", "read") == "cited"


def test_the_common_case_collapses_to_unread():
    """1043 of the 1045 real vault articles look exactly like this."""
    out = migrate_source(
        note('"status": "processed", "read_status": "unread", "processed": "2026-06-30"')
    )
    line = props_of(out)
    assert '"status": "unread"' in line
    assert "read_status" not in line
    # The "processed" *key* carries the import date and must survive intact —
    # only the "processed" *value* of status was legacy.
    assert '"processed": "2026-06-30"' in line


def test_a_real_reading_state_survives_from_either_slot():
    # read_status is the truthful one (the FreeCAD article in the real vault).
    assert '"status": "read"' in props_of(
        migrate_source(note('"status": "processed", "read_status": "read"'))
    )
    # status is the truthful one (the "$1200 за 2,5 месяца" article).
    assert '"status": "read"' in props_of(
        migrate_source(note('"status": "read", "read_status": "unread"'))
    )


def test_a_lone_legacy_status_is_folded():
    out = props_of(migrate_source(note('"status": "processed", "url": "https://e.com"')))
    assert '"status": "unread"' in out
    assert '"url": "https://e.com"' in out


def test_unrelated_status_vocabularies_are_left_alone():
    """19 vault notes use status for something that is not reading triage."""
    for value in ["ok", "success", "warning", "escalated", "resolved"]:
        source = note(f'"status": "{value}"')
        assert migrate_source(source) == source


def test_a_note_with_no_status_is_untouched():
    source = note('"url": "https://e.com", "author": none')
    assert migrate_source(source) == source


def test_read_status_alone_becomes_status():
    out = props_of(migrate_source(note('"read_status": "read", "url": "https://e.com"')))
    assert '"status": "read"' in out
    assert "read_status" not in out


def test_migration_is_idempotent():
    once = migrate_source(
        note('"status": "processed", "read_status": "read", "processed": "2026-06-30"')
    )
    assert migrate_source(once) == once


def test_only_the_header_is_touched():
    """A body that happens to mention the same strings must not be rewritten."""
    source = note('"status": "processed", "read_status": "unread"')
    source += '\nThe pipeline sets "status": "processed" on import.\n'
    out = migrate_source(source)
    assert out.endswith('The pipeline sets "status": "processed" on import.\n')
    assert '"status": "unread"' in props_of(out)
