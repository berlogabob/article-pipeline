import hashlib

from article_pipeline.metadata import ArticleMetadata, FALLBACK_GUIDANCE
from article_pipeline.stage3_render_typst import (
    build_header,
    escape_markup,
    markdown_to_typst,
    note_id,
    render_typst_note,
)
from article_pipeline.tag_scan import VaultNote


def _meta(**overrides):
    base = dict(
        summary_ru="Краткое содержание",
        tags=["ai", "python"],
        author="Jane Doe",
        verification_notes="Проверено",
        is_tutorial=True,
        step_by_step_guidance="1. Шаг",
    )
    base.update(overrides)
    return ArticleMetadata(**base)


def test_note_id_matches_typstseq_formula():
    url = "https://example.com/article"
    expected = "md-" + hashlib.sha256(url.encode()).hexdigest()[:16]
    assert note_id(url) == expected
    assert len(note_id(url)) == len("md-") + 16


def test_header_byte_for_byte():
    header = build_header(
        "md-0123456789abcdef",
        'Title with "quotes" and \\backslash',
        "2026-07-18",
        ["ai", "python"],
        {"status": "processed", "url": "https://e.com", "author": None},
    )
    expected = (
        '#import "/_system/tylog.typ" as tylog\n'
        "\n"
        "#show: tylog.note.with(\n"
        '  id: "md-0123456789abcdef",\n'
        '  title: "Title with \\"quotes\\" and \\\\backslash",\n'
        '  kind: "article",\n'
        '  date: "2026-07-18",\n'
        '  tags: ("ai", "python",),\n'
        "  aliases: (),\n"
        "  project: none,\n"
        '  properties: ("status": "processed", "url": "https://e.com", "author": none,),\n'
        ")\n"
    )
    assert header == expected


def test_header_empty_tags_and_no_date():
    header = build_header("md-x", "T", None, [], {})
    assert "  tags: (),\n" in header
    assert "  date: none,\n" in header
    assert "  properties: (:),\n" in header


def test_escape_markup_covers_import_core_charset():
    assert escape_markup("a-b") == "a\\-b"
    assert escape_markup("#[x]@") == "\\#\\[x\\]\\@"
    assert escape_markup("= + / ~") == "\\= \\+ \\/ \\~"
    assert escape_markup("обычный текст") == "обычный текст"


def test_markdown_conversion_basics():
    md = "# Head\n\nPara with **bold** and [link](https://e.com/x).\n\n- item one\n1. numbered\n\n---\n"
    out = markdown_to_typst(md, {})
    assert "== Head" in out
    assert "#strong[bold]" in out
    assert '#link("https://e.com/x")[link]' in out
    assert "- item one" in out
    assert "+ numbered" in out
    assert "#line(length: 100%)" in out


def test_image_mapping_and_fallback():
    md = "![diagram](https://e.com/a.png) and ![gone](https://e.com/b.png)"
    out = markdown_to_typst(md, {"https://e.com/a.png": "/assets/articles/md-x/1.png"})
    assert '#image("/assets/articles/md-x/1.png")' in out
    assert "b.png" not in out  # undownloaded image degrades to alt text
    assert "gone" in out


def test_full_note_sections_and_related():
    nid, src = render_typst_note(
        title="Test Article",
        url="https://example.com/article",
        metadata=_meta(),
        body_markdown="Body text.",
        source="example.com",
        related=[VaultNote("md-aaaa", "Other Note", frozenset({"ai"}))],
        date="2026-07-18",
        llm_provider="ollama",
        llm_model="qwen3.5:9b",
    )
    assert nid == note_id("https://example.com/article")
    assert "= Test Article" in src
    assert "== Summary" in src
    assert "== Guidance" in src
    assert "== Verification" in src
    assert "== Related" in src
    assert '#tylog.ref-note("md-aaaa")[Other Note]' in src
    assert '"llm_provider": "ollama"' in src
    assert '"source": "example.com"' in src


def test_fallback_guidance_hidden():
    _, src = render_typst_note(
        title="T",
        url="https://e.com",
        metadata=_meta(step_by_step_guidance=FALLBACK_GUIDANCE),
        body_markdown="x",
    )
    assert "== Guidance" not in src
