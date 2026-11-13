"""Shared article-title selection rules, usable without parser dependencies."""
import json
import re
import unicodedata
from urllib.parse import urlparse

_SHELL = {"sign in", "log in", "login", "webmail login", "cpanel login", "google search", "javascript is not available", "build software better, together", "hugging face – the ai community building the future", "verifying device"}
_GENERIC = _SHELL | {"url", "untitled", "home", "index", "article", "reddit post", "youtube", "github", "openai", "qwen", "хабр", "x", "twitter", "overview", "redirecting", "prove your humanity", "sign in to your account", "claude artifact", "qwen studio"}


def _fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text).casefold() if c.isalnum())


def _host(url):
    return (urlparse(url if '://' in url else '//' + url).hostname or '').lower()


def normalise_title(text, max_chars=200):
    text = ' '.join((text or '').split()).strip(' "\'“”‘’«»').rstrip(' |–—-·:')
    if len(text) > max_chars:
        text = text[:max_chars]
        if ' ' in text:
            text = text.rsplit(' ', 1)[0]
        text = text.rstrip(' .|–—-·,;:')
    return text


def strip_site_affixes(title, names, host):
    text = normalise_title(title)
    host = _host(host)
    known = {_fold(n) for n in set(names or ()) | {host, host.removeprefix('www.'), host.removeprefix('www.').split('.')[0]} if n}
    for pattern, suffix in [(r'^(.*?)\s+(?:\||-|—|–|·|::|«)\s*(.+)$', True), (r'^(.+?)(?::|\s+\||\s+-|\s+—)\s+(.+)$', False)]:
        match = re.match(pattern, text)
        if match:
            left, right = match.groups()
            affix, remainder = (right, left) if suffix else (left, right)
            if _fold(affix) in known and sum(c.isalpha() for c in remainder) >= 4:
                return normalise_title(remainder)
    return text


def is_generic_title(title, host='', names=()):
    text = normalise_title(title)
    if not text:
        return 'empty'
    host = _host(host)
    folded = _fold(text)
    if folded in {_fold(n) for n in set(names or ()) | {host, host.removeprefix('www.'), host.removeprefix('www.').split('.')[0]} if n}:
        return 'site name'
    lower = text.casefold().rstrip('.… ').strip()
    if lower in _GENERIC or not lower:
        return 'generic'
    if re.fullmatch(r'\d+|[0-9a-fA-F]{6,}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}', text) or (re.fullmatch(r'[A-Za-z0-9_-]{8,16}', text) and re.search(r'[A-Za-z]', text) and re.search(r'\d', text)):
        return 'id'
    if re.fullmatch(r'\d{1,2}:\d{2}(?::\d{2})?|\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?', text):
        return 'timestamp'
    if re.fullmatch(r'[^\s.]+(?:\.[^\s.]+)*\.[A-Za-zа-яА-Я]{2,}', text) or re.search(r'\s+on\s+(?:X|Twitter|Threads|Instagram)$', text, re.I):
        return 'generic'
    if sum(c.isalpha() for c in text) < 4:
        return 'short'
    return None


def _json_nodes(soup):
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            yield value
            for item in value.values():
                if isinstance(item, (dict, list)):
                    yield from walk(item)
    for script in soup.find_all('script', type=re.compile('ld\\+json', re.I)):
        try:
            yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue


def _types(node):
    value = node.get('@type', [])
    return [value] if isinstance(value, str) else value if isinstance(value, list) else []


def site_names(html, url):
    from bs4 import BeautifulSoup
    host = _host(url)
    bare_host = host.removeprefix('www.')
    names = {host, bare_host, bare_host.split('.')[0]} - {''}
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup.find_all('meta'):
        if (tag.get('property') or tag.get('name') or '').lower() in {'og:site_name', 'application-name'} and tag.get('content'):
            names.add(str(tag['content']))
    for node in _json_nodes(soup):
        if 'WebSite' in _types(node) and isinstance(node.get('name'), str):
            names.add(node['name'])
        publisher = node.get('publisher')
        if isinstance(publisher, dict) and isinstance(publisher.get('name'), str):
            names.add(publisher['name'])
    return names


def html_title_candidates(html):
    from bs4 import BeautifulSoup
    import trafilatura
    soup = BeautifulSoup(html or '', 'html.parser')
    candidates = []
    for key in ('og:title', 'twitter:title'):
        for tag in soup.find_all('meta'):
            if (tag.get('property') or tag.get('name') or '').lower() == key and tag.get('content'):
                candidates.append((key, str(tag['content'])))
    for node in _json_nodes(soup):
        if set(_types(node)) & {'Article', 'NewsArticle', 'BlogPosting', 'TechArticle'} and isinstance(node.get('headline'), str):
            candidates.append(('json-ld', node['headline']))
    h1 = soup.select_one('article h1, main h1') or soup.find('h1')
    if h1:
        candidates.append(('h1', h1.get_text(' ', strip=True)))
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.title:
            candidates.append(('trafilatura', metadata.title))
    except Exception:
        pass
    if soup.title:
        candidates.append(('title', soup.title.get_text(' ', strip=True)))
    return candidates


def choose_title(html, url):
    names = site_names(html, url)
    for _, title in html_title_candidates(html):
        title = strip_site_affixes(title, names, url)
        if not is_generic_title(title, url, names):
            return title
    return None


def summary_title(summary, max_chars=80):
    text = normalise_title(summary, max_chars=100000)
    text = re.split(r'(?<=[.!?])\s+', text, maxsplit=1)[0]
    stripped = re.sub(r'^(?:Автор описывает|Автор рассказывает о|В статье|Статья о|The article describes)\s+', '', text, flags=re.I)
    if len(stripped) >= 25:
        text = stripped
    if len(text) > max_chars:
        part = text[:max_chars]
        clauses = list(re.finditer(r'[,;:]', part))
        if clauses and clauses[-1].start() >= 35:
            text = part[:clauses[-1].start()]
        else:
            text = part.rsplit(' ', 1)[0] if ' ' in part else part
    text = text.rstrip(' .,!?:;–—-')
    function_words = {'в', 'и', 'на', 'с', 'к', 'о', 'об', 'от', 'до', 'за', 'из', 'по', 'у', 'а', 'но', 'где', 'of', 'the', 'and', 'or', 'to', 'in', 'on', 'for', 'by', 'a', 'an', 'as', 'at'}
    while text and text.split()[-1].casefold() in function_words:
        text = text.rsplit(' ', 1)[0] if ' ' in text else ''
    text = text.rstrip(' .,!?:;–—-')
    return text[:1].upper() + text[1:]


def llm_title(engine, text, model, fallback_model=None):
    prompt = 'Return JSON {"title": "..."}: a concise title of the article topic, max 80 characters, in the same language as the text, without a site name.\n\n' + (text or '')[:8000]
    for selected in dict.fromkeys([model, fallback_model] if fallback_model else [model]):
        try:
            raw = engine.chat_json(prompt, selected or '', 0.05)
            decoder = json.JSONDecoder()
            for i, c in enumerate(raw):
                if c == '{':
                    try:
                        data, _ = decoder.raw_decode(raw[i:])
                        title = data.get('title') if isinstance(data, dict) else None
                        if isinstance(title, str):
                            title = normalise_title(title, 80)
                            if not is_generic_title(title):
                                return title
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
    return None


def final_title(resolved, passed_title, url, summary, text, engine, cfg):
    host = _host(url).removeprefix('www.')
    names = {host, host.split('.')[0]}
    for title in (resolved, passed_title):
        title = strip_site_affixes(title or '', names, url)
        if not is_generic_title(title, url, names):
            return title
    title = llm_title(engine, text, cfg.engine.primary_model, cfg.engine.fallback_model)
    if title and not is_generic_title(title, url, names):
        return title
    title = summary_title(summary)
    return title if title and not is_generic_title(title, url, names) else _host(url).removeprefix('www.')
