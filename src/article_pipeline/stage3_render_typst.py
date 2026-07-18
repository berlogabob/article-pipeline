"""Render an article as a TyLog Format v1 Typst note.

Header serialization mirrors TypstSeq's scanner.dart (_typstString/_typstList/
_typstDictionary, fixed field order) byte-for-byte — the app has a regex
fallback parser over the literal header text, so textual fidelity matters.
Body escaping uses the exact character set of tylog_import_core (lib.rs).
"""

import hashlib
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .metadata import ArticleMetadata, FALLBACK_GUIDANCE
from .tag_scan import VaultNote

# tylog_import_core escape set
_ESCAPE_CHARS = set('\\#$*_`<>@[]~=-+/')


def note_id(url: str) -> str:
    return "md-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# --- header serialization (scanner.dart parity) ---

def _typst_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _typst_list(values: List[str]) -> str:
    if not values:
        return "()"
    return "(" + ", ".join(_typst_string(v) for v in values) + ",)"


def _typst_value(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _typst_string(value)
    if isinstance(value, (list, tuple)):
        return _typst_list([str(v) for v in value])
    if isinstance(value, dict):
        return _typst_dict(value)
    return _typst_string(str(value))


def _typst_dict(values: Dict) -> str:
    if not values:
        return "(:)"
    parts = ", ".join(
        f"{_typst_string(str(k))}: {_typst_value(v)}" for k, v in values.items()
    )
    return "(" + parts + ",)"


def build_header(
    nid: str,
    title: str,
    date: Optional[str],
    tags: List[str],
    properties: Dict,
) -> str:
    return (
        '#import "/_system/tylog.typ" as tylog\n'
        "\n"
        "#show: tylog.note.with(\n"
        f"  id: {_typst_string(nid)},\n"
        f"  title: {_typst_string(title)},\n"
        '  kind: "article",\n'
        f"  date: {_typst_value(date)},\n"
        f"  tags: {_typst_list(tags)},\n"
        "  aliases: (),\n"
        "  project: none,\n"
        f"  properties: {_typst_dict(properties)},\n"
        ")\n"
    )


# --- body conversion (markdown from trafilatura -> typst markup) ---

def escape_markup(text: str) -> str:
    return "".join("\\" + c if c in _ESCAPE_CHARS else c for c in text)


_INLINE_TOKEN_RE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<image>!\[(?P<img_alt>[^\]]*)\]\((?P<img_url>[^)\s]+)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<bold>\*\*(?P<bold_text>[^*\n]+)\*\*)"
    r"|(?P<italic>\*(?P<italic_text>[^*\n]+)\*)"
)


def _render_inline(text: str, image_map: Dict[str, str]) -> str:
    out: List[str] = []
    pos = 0
    for m in _INLINE_TOKEN_RE.finditer(text):
        out.append(escape_markup(text[pos:m.start()]))
        if m.group("code"):
            out.append(m.group("code"))  # typst inline raw shares backtick syntax
        elif m.group("image"):
            local = image_map.get(m.group("img_url"))
            if local:
                out.append(f'#image({_typst_string(local)})')
            elif m.group("img_alt"):
                out.append(escape_markup(m.group("img_alt")))
        elif m.group("link"):
            url = m.group("link_url")
            label = _render_inline(m.group("link_text"), image_map)
            out.append(f"#link({_typst_string(url)})[{label}]")
        elif m.group("bold"):
            out.append(f"#strong[{escape_markup(m.group('bold_text'))}]")
        elif m.group("italic"):
            out.append(f"#emph[{escape_markup(m.group('italic_text'))}]")
        pos = m.end()
    out.append(escape_markup(text[pos:]))
    return "".join(out)


_HEADING_RE = re.compile(r"^(#{1,5})\s+(.*)$")
_ULIST_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OLIST_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")


def markdown_to_typst(md: str, image_map: Dict[str, str]) -> str:
    lines: List[str] = []
    in_fence = False
    for line in md.splitlines():
        stripped = line.rstrip()
        if stripped.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append(stripped.lstrip())
            continue
        if in_fence:
            lines.append(line)
            continue
        if not stripped:
            lines.append("")
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            # article title is level 1 (=), so markdown # starts at ==
            lines.append("=" * (len(m.group(1)) + 1) + " " + _render_inline(m.group(2), image_map))
            continue
        if _HR_RE.match(stripped):
            lines.append("#line(length: 100%)")
            continue
        m = _ULIST_RE.match(stripped)
        if m:
            lines.append(m.group(1) + "- " + _render_inline(m.group(2), image_map))
            continue
        m = _OLIST_RE.match(stripped)
        if m:
            lines.append(m.group(1) + "+ " + _render_inline(m.group(2), image_map))
            continue
        if stripped.startswith("> "):
            lines.append("#quote(block: true)[" + _render_inline(stripped[2:], image_map) + "]")
            continue
        lines.append(_render_inline(stripped, image_map))
    return "\n".join(lines).strip() + "\n"


# --- note assembly ---

def render_typst_note(
    title: str,
    url: str,
    metadata: ArticleMetadata,
    body_markdown: str,
    source: str = "",
    image_map: Optional[Dict[str, str]] = None,
    related: Optional[List[VaultNote]] = None,
    date: Optional[str] = None,
    llm_provider: str = "",
    llm_model: str = "",
    is_youtube: bool = False,
) -> Tuple[str, str]:
    """Returns (note_id, typst_source)."""
    image_map = image_map or {}
    nid = note_id(url)
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    properties: Dict = {
        "status": "processed",
        "read_status": "unread",
        "processed": date,
        "url": url,
        "source": "youtube" if is_youtube else (source or ""),
        "author": metadata.author,
        "import_format": "url",
    }
    if llm_provider:
        properties["llm_provider"] = llm_provider
    if llm_model:
        properties["llm_model"] = llm_model

    parts = [
        build_header(nid, title, date, metadata.tags, properties),
        "",
        "= " + escape_markup(title),
        "",
        "== Summary",
        "",
        escape_markup(metadata.summary_ru.strip()),
        "",
    ]

    guidance = (metadata.step_by_step_guidance or "").strip()
    if guidance and guidance != FALLBACK_GUIDANCE:
        parts += ["== Guidance", "", escape_markup(guidance), ""]

    if metadata.verification_notes.strip():
        parts += ["== Verification", "", escape_markup(metadata.verification_notes.strip()), ""]

    parts += ["#line(length: 100%)", "", markdown_to_typst(body_markdown, image_map)]

    if related:
        parts += ["", "== Related", ""]
        for note in related:
            parts.append(
                f"- #tylog.ref-note({_typst_string(note.id)})[{escape_markup(note.title)}]"
            )
        parts.append("")

    return nid, "\n".join(parts)
