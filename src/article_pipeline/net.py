"""URL handling, filesystem-safe naming, and misc text utilities.

Ported from the old logseq-processor project's src/utils.py, with all
Config-singleton dependencies removed: every tunable is now an explicit
function parameter with a default matching the old config value.
"""

import logging
import re
import shutil
import socket
import time
import unicodedata
from ipaddress import ip_address
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("article_pipeline")


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.startswith("m."):
        netloc = netloc[2:]

    path = parsed.path.rstrip("/")
    if not path:
        path = "/"

    normalized = f"{parsed.scheme.lower()}://{netloc}{path}"

    normalized = re.sub(r":80(?=/|$)", "", normalized)
    normalized = re.sub(r":443(?=/|$)", "", normalized)

    if "#" in normalized:
        normalized = normalized.split("#")[0]

    query_params_to_keep = {"v", "p", "id", "q", "search"}
    parsed2 = urlparse(normalized)
    if parsed2.query:
        params = []
        for param in parsed2.query.split("&"):
            if "=" in param:
                key = param.split("=")[0]
                if key.lower() in query_params_to_keep:
                    params.append(param)
        if params:
            normalized = f"{normalized.split('?')[0]}?{'&'.join(params)}"
        else:
            normalized = normalized.split("?")[0]

    return normalized


def get_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if domain.startswith("m."):
        domain = domain[2:]
    return domain


def extract_url_from_text(text: str) -> Optional[str]:
    urls = re.findall(r"https?://[^\s<>\"'\)]+", text)
    for url in urls:
        url = url.rstrip(".,;:!?")
        if len(url) > 10:
            return url
    return None


def is_processed(path: Path) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8", errors="ignore")
    return "title::" in content and "type:: article" in content


def move_to_folder(
    source: Path, target_folder: Path, error_suffix: Optional[str] = None
) -> Optional[Path]:
    target_folder.mkdir(parents=True, exist_ok=True)

    if error_suffix:
        name = source.stem + error_suffix + source.suffix
    else:
        name = source.name

    dest = target_folder / name
    counter = 1
    while dest.exists():
        if error_suffix:
            name = f"{source.stem}{error_suffix}_{counter}{source.suffix}"
        else:
            name = f"{source.stem}_{counter}{source.suffix}"
        dest = target_folder / name
        counter += 1

    try:
        shutil.move(str(source), str(dest))
        logger.info("Moved: %s → %s", source.name, dest)
        return dest
    except Exception as e:
        logger.error("Failed to move %s to %s: %s", source.name, target_folder, e)
        return None


def count_non_empty_lines(path: Path) -> int:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        return sum(1 for line in content.splitlines() if line.strip())
    except Exception:
        return 999


def clean_json(raw: str) -> str:
    raw = re.sub(r"```json|```|json", "", raw, flags=re.DOTALL | re.I)
    raw = re.sub(r"```json|```|^json\s*", "", raw, flags=re.MULTILINE | re.IGNORECASE)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    return raw[start:end] if start >= 0 and end > start else "{}"


# ---------------------------------------------------------------------------
# URL resolution / expansion (shortlinks, wrapper redirects, SSRF guards)
# ---------------------------------------------------------------------------

_WRAPPER_PARAM_KEYS = {"url", "u", "target", "redirect", "dest", "to"}
_JS_REDIRECT_PATTERNS = [
    r"""window\.location(?:\.href)?\s*=\s*["']([^"']+)["']""",
    r"""location\.replace\(\s*["']([^"']+)["']\s*\)""",
    r"""location\.assign\(\s*["']([^"']+)["']\s*\)""",
]


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_private_or_local_ip(ip_str: str) -> bool:
    try:
        ip_obj = ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return False
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_unspecified
    )


def _hostname_resolves_to_private(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    except OSError:
        return False

    for info in infos:
        addr = info[4][0]
        if _is_private_or_local_ip(addr):
            return True
    return False


def _is_safe_public_http_url(url: str) -> bool:
    if not _is_http_url(url):
        return False

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().strip("[]").lower()
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    if _is_private_or_local_ip(hostname):
        return False
    if _hostname_resolves_to_private(hostname):
        return False
    return True


def _extract_query_wrapped_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in _WRAPPER_PARAM_KEYS:
        values = query.get(key)
        if not values:
            continue
        for value in values:
            candidate = value.strip()
            for _ in range(2):
                candidate = unquote(candidate).strip()
            if _is_safe_public_http_url(candidate):
                return candidate
    return None


def _is_likely_wrapper_url(url: str) -> bool:
    parsed = urlparse(url)
    if any(k in _WRAPPER_PARAM_KEYS for k in parse_qs(parsed.query).keys()):
        return True
    path = parsed.path.lower()
    return any(marker in path for marker in ("/redirect", "/out", "/link"))


def _extract_html_fallback_url(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    if canonical and canonical.get("href"):
        candidates.append(canonical.get("href", "").strip())

    og_url = soup.find("meta", attrs={"property": "og:url"})
    if og_url and og_url.get("content"):
        candidates.append(og_url.get("content", "").strip())

    refresh_meta = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
    if refresh_meta and refresh_meta.get("content"):
        match = re.search(r"url\s*=\s*([^;]+)$", refresh_meta["content"], flags=re.I)
        if match:
            candidates.append(match.group(1).strip().strip("\"'"))

    for pattern in _JS_REDIRECT_PATTERNS:
        match = re.search(pattern, html, flags=re.I)
        if match:
            candidates.append(match.group(1).strip())

    for candidate in candidates:
        if not candidate:
            continue
        absolute = urljoin(base_url, candidate)
        if _is_safe_public_http_url(absolute):
            return absolute
    return None


def resolve_url(url: str, timeout: int = 10, max_redirects: int = 5) -> Optional[str]:
    if not url:
        return None

    session = requests.Session()
    try:
        session.max_redirects = max_redirects
        current = url
        response = None

        try:
            head_resp = session.head(current, allow_redirects=True, timeout=timeout)
            if head_resp.url:
                current = head_resp.url
        except requests.RequestException:
            try:
                response = session.get(
                    current, allow_redirects=True, timeout=timeout, stream=True
                )
                if response.url:
                    current = response.url
            except requests.RequestException:
                return None

        wrapped = _extract_query_wrapped_url(current)
        if wrapped:
            if response is not None:
                response.close()
            return wrapped

        if _is_likely_wrapper_url(current):
            try:
                if response is None:
                    response = session.get(
                        current, allow_redirects=True, timeout=timeout, stream=True
                    )
                html = response.text
                meta_target = _extract_html_fallback_url(html, current)
                if meta_target:
                    return meta_target
            except Exception:
                pass
            finally:
                if response is not None:
                    response.close()
                    response = None

        if response is not None:
            response.close()

        if not _is_safe_public_http_url(current):
            return None
        return current
    finally:
        session.close()


def expand_url(url: str, timeout: int = 10, max_redirects: int = 5) -> Optional[str]:
    return resolve_url(url, timeout=timeout, max_redirects=max_redirects)


# ---------------------------------------------------------------------------
# Nextcloud-safe filenames
# ---------------------------------------------------------------------------

# Characters explicitly forbidden on common Nextcloud/Windows-backed storage.
_FORBIDDEN_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
# Keep Unicode letters/digits (incl. Cyrillic), spaces, and - _ .
# Everything else (emoji, pictographs, symbols, control chars, etc.) is stripped.
_ALLOWED_CHAR_RE = re.compile(r"[^\w\s\-_.]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def safe_filename(name: str, max_chars: int = 60) -> str:
    """Sanitize `name` into a Nextcloud-safe filename stem.

    Keeps Unicode letters (including Cyrillic) and digits, spaces, and
    ``- _ .``; strips everything else including emoji/symbols/pictographs
    and the characters forbidden on common filesystems (``< > : " / \\ | ? *``).
    Collapses whitespace runs, strips leading/trailing spaces and dots, and
    truncates to `max_chars`, cutting at a word boundary when possible (no
    ellipsis). Falls back to "Article" if the result is empty.
    """
    if not name:
        return "Article"

    # Normalize so combining marks / compatibility forms don't leak through.
    text = unicodedata.normalize("NFC", name)
    text = _FORBIDDEN_CHARS_RE.sub("", text)

    # Strip anything that isn't a "word" char (\w already includes Unicode
    # letters/digits/underscore under re.UNICODE), whitespace, hyphen, or dot.
    # \w also matches underscore, which we want to keep anyway.
    cleaned_chars = []
    for ch in text:
        if ch.isalnum() or ch in " -_.":
            cleaned_chars.append(ch)
        # else: drop (emoji, symbols, punctuation, control chars, etc.)
    cleaned = "".join(cleaned_chars)

    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = cleaned.strip(" .")

    if not cleaned:
        return "Article"

    if len(cleaned) > max_chars:
        truncated = cleaned[:max_chars]
        # Cut at a word boundary when possible.
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        cleaned = truncated.strip(" .")

    if not cleaned:
        return "Article"

    return cleaned


def get_unique_path(
    title: str,
    folder: Path,
    source: str = "",
    ext: str = ".md",
    max_chars: int = 60,
) -> Path:
    source = source.strip() if source else ""
    formatted = f"{source} - {title}" if source else title
    stem = safe_filename(formatted, max_chars=max_chars)

    path = folder / f"{stem}{ext}"
    counter = 1
    while path.exists():
        path = folder / f"{stem} ({counter}){ext}"
        counter += 1
    return path
