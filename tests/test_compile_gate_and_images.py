"""The compile gate drops notes Typst rejects; image bytes decide the
extension; attribution maps by filename stem, not zip order."""

import shutil

import pytest

from article_pipeline.images import _extension_from_magic
from article_pipeline.processor import typst_compile_error

STUB_TYLOG = """
#let note(..args) = doc => doc
#let ref-note(id) = (body) => body
"""


@pytest.fixture
def mini_vault(tmp_path):
    (tmp_path / "_system").mkdir()
    (tmp_path / "_system" / "tylog.typ").write_text(STUB_TYLOG, encoding="utf-8")
    (tmp_path / "articles").mkdir()
    return tmp_path


needs_typst = pytest.mark.skipif(shutil.which("typst") is None, reason="no typst binary")


@needs_typst
def test_valid_note_passes_gate(mini_vault):
    note = mini_vault / "articles" / "ok.typ"
    note.write_text(
        '#import "/_system/tylog.typ" as tylog\n'
        "#show: tylog.note.with(id: \"x\")\n= Title\nBody text.\n",
        encoding="utf-8",
    )
    assert typst_compile_error(mini_vault, note) is None


@needs_typst
def test_broken_note_fails_gate(mini_vault):
    note = mini_vault / "articles" / "bad.typ"
    note.write_text("= Title\n#strong[x](oops\n", encoding="utf-8")
    err = typst_compile_error(mini_vault, note)
    assert err is not None and "error" in err.lower()


def test_magic_bytes_beat_wrong_extension():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    assert _extension_from_magic(png) == ".png"
    assert _extension_from_magic(b"\xff\xd8\xff\xe0rest") == ".jpg"
    assert _extension_from_magic(b"RIFF\x00\x00\x00\x00WEBPVP8") == ".webp"
    assert _extension_from_magic(b"<svg>") is None


def test_image_map_attribution_by_stem(tmp_path):
    # Simulate: url #1 skipped, urls #2 and #3 saved as 2.jpg / 3.png.
    urls = ["https://a/skip.svg", "https://a/two.jpg", "https://a/three.png"]
    saved = [tmp_path / "2.jpg", tmp_path / "3.png"]
    image_map = {}
    for local in saved:
        orig = urls[int(local.stem) - 1]
        image_map[orig] = f"/assets/x/{local.name}"
    assert image_map == {
        "https://a/two.jpg": "/assets/x/2.jpg",
        "https://a/three.png": "/assets/x/3.png",
    }
