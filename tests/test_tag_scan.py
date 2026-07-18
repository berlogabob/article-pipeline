from pathlib import Path

from article_pipeline.tag_scan import find_related, parse_header, scan_vault, VaultNote


HEADER = """#import "/_system/tylog.typ" as tylog

#show: tylog.note.with(
  id: "md-{i}",
  title: "{title}",
  kind: "article",
  date: none,
  tags: ({tags}),
  aliases: (),
  project: none,
  properties: (:),
)

= Body
"""


def _write(tmp_path: Path, i: str, title: str, tags: list[str]):
    tag_src = ", ".join(f'"{t}"' for t in tags) + ("," if tags else "")
    (tmp_path / f"{title}.typ").write_text(
        HEADER.format(i=i, title=title, tags=tag_src), encoding="utf-8"
    )


def test_parse_header_extracts_fields():
    note = parse_header(HEADER.format(i="abc", title="My Note", tags='"ai", "rust",'))
    assert note == VaultNote("md-abc", "My Note", frozenset({"ai", "rust"}))


def test_parse_header_rejects_headerless_text():
    assert parse_header("just some text") is None


def test_scan_and_related_top_k(tmp_path):
    _write(tmp_path, "1", "A", ["ai", "python"])
    _write(tmp_path, "2", "B", ["ai"])
    _write(tmp_path, "3", "C", ["cooking"])
    _write(tmp_path, "4", "Self", ["ai", "python"])

    notes = scan_vault(tmp_path)
    assert len(notes) == 4

    related = find_related(["ai", "python"], notes, exclude_id="md-4", max_links=2)
    assert [n.id for n in related] == ["md-1", "md-2"]  # overlap 2, then 1
    assert all(n.id != "md-4" for n in related)


def test_no_tags_means_no_related(tmp_path):
    _write(tmp_path, "1", "A", ["ai"])
    notes = scan_vault(tmp_path)
    assert find_related([], notes) == []


def test_scan_cap(tmp_path):
    for i in range(3):
        _write(tmp_path, str(i), f"N{i}", ["x"])
    assert scan_vault(tmp_path, max_files=2) == []
