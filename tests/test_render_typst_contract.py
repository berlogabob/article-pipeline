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
    assert '"llm_provider": "ollama"' in src
    assert '"source": "example.com"' in src
    # A fresh import enters the app's reading pipeline at "unread"; the app has
    # no "read_status" key.
    assert '"status": "unread"' in src
    assert "read_status" not in src
    # The Related block must be the exact shape TypstSeq's stripAutoRelated
    # recognises: blank line, marker on its own line, heading, refs, trailing
    # newline. Anything else makes "Relink vault" stack a second block.
    assert src.endswith(
        "\n\n// tylog:auto-related\n"
        "== Related\n"
        '- #tylog.ref-note("md-aaaa")[Other Note]\n'
    )


def test_related_block_is_replaceable_by_the_app_stripper():
    """Port of TypstSeq's stripAutoRelated, so the two stay in lockstep."""
    import re

    from article_pipeline.stage3_render_typst import AUTO_RELATED_MARKER

    def strip_auto_related(source: str) -> str:
        m = re.search(f"(?:^|\n){re.escape(AUTO_RELATED_MARKER)}(?:\r?\n|$)", source)
        if not m:
            return source
        return re.sub(r"[\r\n]+$", "", source[: m.start()])

    _, with_block = render_typst_note(
        title="T",
        url="https://e.com",
        metadata=_meta(),
        body_markdown="Body.",
        related=[VaultNote("md-aaaa", "Other Note", frozenset({"ai"}))],
        date="2026-07-18",
    )
    _, without_block = render_typst_note(
        title="T",
        url="https://e.com",
        metadata=_meta(),
        body_markdown="Body.",
        date="2026-07-18",
    )
    # Stripping the generated block returns the note the app would have
    # re-appended onto — so a relink replaces it rather than duplicating it.
    assert strip_auto_related(with_block) == without_block.rstrip("\n")
    assert strip_auto_related(with_block).count("== Related") == 0


def test_fallback_guidance_hidden():
    _, src = render_typst_note(
        title="T",
        url="https://e.com",
        metadata=_meta(step_by_step_guidance=FALLBACK_GUIDANCE),
        body_markdown="x",
    )
    assert "== Guidance" not in src


def test_paren_after_bold_or_link_is_escaped():
    from article_pipeline.stage3_render_typst import markdown_to_typst

    out = markdown_to_typst("**PyGhidra**(Python-биндинг Ghidra)", {})
    assert "#strong[PyGhidra];\\(" in out
    out = markdown_to_typst("[label](https://a.example)(Dec 2024)", {})
    assert ')[label];\\(' in out


def test_unpaired_fence_is_demoted_to_text():
    from article_pipeline.stage3_render_typst import markdown_to_typst

    # Odd fence count: the unmatched marker must not invert the tail.
    out = markdown_to_typst("```sh\n# a python comment\n", {})
    assert "```" not in out.splitlines()[0] or out.count("```") % 2 == 0
    assert "== a python comment" in out  # rendered as prose, not raw

    # Balanced fences still pass through raw.
    out = markdown_to_typst("```sh\n# raw\n```\nafter", {})
    assert "# raw" in out and "\\# raw" not in out


def test_text_touching_a_link_gets_a_terminator():
    from article_pipeline.stage3_render_typst import markdown_to_typst

    out = markdown_to_typst("[Схема](https://a.example/s).pdf", {})
    assert '].pdf' not in out
    assert '];\\.pdf' in out or '];.pdf' in out


def test_backticks_inside_fence_get_longer_delimiter():
    from article_pipeline.stage3_render_typst import markdown_to_typst

    out = markdown_to_typst('```python\nx = "```" in prompt\n```\nafter', {})
    first = out.splitlines()[0]
    assert first.startswith("````")  # longer than the inner run
    assert out.splitlines()[1] == 'x = "```" in prompt'
    assert "after" in out
