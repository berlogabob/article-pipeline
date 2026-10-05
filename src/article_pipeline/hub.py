"""Staff-authenticated Idea Hub sync; user prose stays outside managed blocks."""

import json
import logging
import os
import re
import shlex
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .stage3_render_typst import build_header, escape_markup, _typst_string, _typst_value
from .tag_scan import parse_header

BEGIN = '// tylog:hub-begin\n'
END = '// tylog:hub-end\n'
STRING = r'"(?:[^"\\]|\\.)*"'
logger = logging.getLogger('article_pipeline')


def _close(text, start):
    depth, quoted, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
    raise ValueError('Unclosed Typst header')


def _split(text):
    start = text.index('#show: tylog.note.with(') + len('#show: tylog.note.with')
    end = _close(text, start) + 1
    return text[:end], text[end:]


def _property(head, key):
    match = re.search(r'\bproperties:\s*\(', head)
    if not match:
        raise ValueError('Missing properties')
    start = match.end() - 1
    end = _close(head, start)
    value = re.search(r'(?:"' + re.escape(key) + r'"|\b' + re.escape(key) + r')\s*:\s*(' + STRING + r'|\d+|none|true|false)', head[start:end])
    if not value:
        return None
    raw = value.group(1)
    return json.loads(raw) if raw.startswith('"') else raw


def _set_property(head, key, value):
    match = re.search(r'\bproperties:\s*\(', head)
    start = match.end() - 1
    end = _close(head, start)
    props = head[start:end + 1]
    pattern = r'(?:"' + re.escape(key) + r'"|\b' + re.escape(key) + r')\s*:\s*'
    field = re.search(pattern, props)
    if field:
        start_value = field.end()
        if props[start_value] == '(':
            end_value = _close(props, start_value) + 1
        else:
            scalar = re.match(STRING + r'|\d+|none|true|false', props[start_value:])
            if not scalar:
                raise ValueError('Unsupported property value')
            end_value = start_value + scalar.end()
        props = props[:start_value] + _typst_value(value) + props[end_value:]
    else:
        props = props[:-1].rstrip().rstrip(':') + f'{_typst_string(key)}: {_typst_value(value)},)'
    return head[:start] + props + head[end + 1:]


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    # Atomic replacement, shared with the worker's JSON writer pattern.
    import tempfile
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.hub-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _note(path, header, managed):
    body = _split(path.read_text(encoding='utf-8'))[1] if path.exists() else '\n\n'
    block = BEGIN + managed.rstrip() + '\n' + END
    if BEGIN in body:
        start = body.index(BEGIN)
        end = body.index(END, start) + len(END)
        body = body[:start] + block + body[end:]
    else:
        body += '\n' + block
    _write(path, header + body)


def sync_hub(cfg, *, env_path=None, transport=None):
    """One poll. Errors deliberately omit HTTP response bodies and credentials."""
    try:
        return _sync(cfg, env_path or Path('~/.tylog-supabase.env').expanduser(), transport)
    except Exception as exc:
        reason = 'unexpected failure'
        if isinstance(exc, httpx.HTTPStatusError):
            reason = f'HTTP {exc.response.status_code}'
        elif isinstance(exc, httpx.RequestError):
            reason = 'HTTP transport failed'
        elif isinstance(exc, OSError):
            reason = 'filesystem operation failed'
        elif isinstance(exc, KeyError):
            reason = 'missing configuration or response field'
        elif isinstance(exc, ValueError):
            reason = str(exc) if str(exc) in {
                'Staff identity unavailable', 'Submitted idea unavailable',
                'Duplicate hub_id', 'Status update unavailable', 'Missing properties',
                'Unclosed Typst header', 'Unsupported property value',
            } else 'invalid data'
        raise RuntimeError(f'Hub sync failed ({type(exc).__name__}: {reason})') from None


def _sync(cfg, env_path, transport):
    from .worker import write_job

    env = {}
    for line in Path(env_path).read_text(encoding='utf-8').splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'export':
            parts = parts[1:]
        if parts:
            key, value = ' '.join(parts).split('=', 1)
            env[key] = value
    vault = Path(cfg.typst.vault_dir).expanduser().resolve()
    state_path = vault / '_system/hub-sync.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state.setdefault('ideas', {})
    state.setdefault('pending', {})
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Single-Mac poll lock prevents overlapping submissions.
    import fcntl
    with (state_path.parent / '.hub.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        service_key = env.get('SUPABASE_SERVICE_KEY')
        with httpx.Client(base_url=env['SUPABASE_URL'].rstrip('/'), timeout=30,
                          transport=transport, headers={'apikey': service_key or env['SUPABASE_ANON_KEY']}) as client:
            def request(method, route, **kwargs):
                response = client.request(method, route, **kwargs)
                response.raise_for_status()
                return response.json() if response.content else None

            if service_key:
                client.headers['Authorization'] = 'Bearer ' + service_key
                identity = {'email': 'eq.' + env['STAFF_EMAIL']}
            else:
                auth = request('POST', '/auth/v1/token?grant_type=password', json={
                    'email': env['STAFF_EMAIL'], 'password': env['STAFF_PASSWORD']})
                client.headers['Authorization'] = 'Bearer ' + auth['access_token']
                identity = {'auth_user_id': 'eq.' + auth['user']['id']}

            def rows(table, **filters):
                # PostgREST caps pages; explicitly paginate rather than silently dropping notes.
                result = []
                while True:
                    page = request('GET', '/rest/v1/' + table, params={
                        'select': '*', 'order': 'id' if table != 'idea_matches' else 'idea_a,idea_b,kind',
                        'limit': 1000, 'offset': len(result), **filters})
                    result.extend(page)
                    if not page:
                        return result

            staff = rows('people', **identity, is_staff='eq.true')
            if len(staff) != 1:
                raise ValueError('Staff identity unavailable')
            me = staff[0]
            people = {p['id']: p for p in rows('people')}
            consultations = rows('activities', kind='eq.consultation', owner_staff_id=f"eq.{me['id']}")
            ideas = rows('ideas')
            by_id = {str(i['id']): i for i in ideas}
            matches = rows('idea_matches')
            local = {}
            skipped = 0
            for path in sorted(vault.rglob('*.typ')):
                if '_system' in path.relative_to(vault).parts:
                    continue
                try:
                    text = path.read_text(encoding='utf-8')
                    if '#show: tylog.note.with(' not in text:
                        continue
                    head, body = _split(text)
                    note = parse_header(head)
                    if not note:
                        raise ValueError('Invalid note header')
                    if not (re.search(r'\bkind:\s*"idea"', head) or {'idea', '#idea'} & note.tags):
                        continue
                    hub_id = _property(head, 'hub_id')
                    status = _property(head, 'status')
                    props = {k: _property(head, k) for k in ('link', 'can_bring', 'looking_for')}
                except (OSError, ValueError):
                    skipped += 1
                    continue
                if not hub_id:
                    key = path.relative_to(vault).as_posix()
                    token = state['pending'].get(key)
                    if not token:
                        token = request('POST', '/rest/v1/rpc/submit_idea', json={
                            'p_name': me['name'], 'p_email': me['email'], 'p_body': body.strip(),
                            'p_link': props['link'], 'p_can_bring': props['can_bring'],
                            'p_looking_for': props['looking_for'],
                            'p_student_number': None, 'p_website': None})
                        state['pending'][key] = token
                        write_job(state_path, state)
                    found = rows('ideas', status_token='eq.' + token)
                    if len(found) != 1:
                        raise ValueError('Submitted idea unavailable')
                    idea = found[0]
                    hub_id = str(idea['id'])
                    by_id[hub_id] = idea
                    head = _set_property(head, 'hub_id', hub_id)
                    _write(path, head + body)
                    state['pending'].pop(key, None)
                    write_job(state_path, state)
                hub_id = str(hub_id)
                if hub_id in local:
                    raise ValueError('Duplicate hub_id')
                local[hub_id] = path
                old = state['ideas'].get(hub_id)
                idea = by_id.get(hub_id)
                if idea and status in ('new', 'approved', 'archived') and status != (old or idea)['status']:
                    updated = request('PATCH', '/rest/v1/ideas', params={'id': 'eq.' + hub_id},
                                      headers={'Prefer': 'return=representation'}, json={'status': status})
                    if len(updated) != 1:
                        raise ValueError('Status update unavailable')
                    by_id[hub_id] = updated[0]

            logger.info('Hub vault scan skipped %d unparseable notes', skipped)

            for c in consultations:
                start = datetime.fromisoformat(c['starts_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Lisbon'))
                end = datetime.fromisoformat(c['ends_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Lisbon'))
                nid = f"consult-{c['id']}"
                existing = list((vault / 'events').rglob(nid + '.typ'))
                path = existing[0] if existing else vault / f'events/{start:%Y/%m}/{nid}.typ'
                student = people.get(c['requester_id'], {}).get('name', c.get('requester_display') or '')
                props = {'event_type': 'consultation', 'student': student, 'student_id': c['requester_id'],
                         'start': start.strftime('%H:%M'), 'end': end.strftime('%H:%M'),
                         'starts_at': c['starts_at'], 'ends_at': c['ends_at'], 'status': c['status']}
                head = _split(path.read_text())[0] if path.exists() else build_header(
                    nid, 'Consultation: ' + student, start.date().isoformat(), [], {}, kind='event', project=c.get('purpose'))
                head = re.sub(r'(?m)^(\s*date:\s*)[^\n]*', lambda m: m[1] + _typst_value(start.date().isoformat()) + ',', head)
                head = re.sub(r'(?m)^(\s*project:\s*)[^\n]*', lambda m: m[1] + _typst_value(c.get('purpose')) + ',', head)
                for k, v in props.items():
                    head = _set_property(head, k, v)
                _note(path, head, escape_markup(f"{student}\n{c.get('purpose') or ''}\n{start.isoformat()} – {end.isoformat()}\n{c['status']}"))

            today = datetime.now(ZoneInfo('Europe/Lisbon')).date().isoformat()
            for iid, idea in by_id.items():
                path = local.get(iid, vault / 'ideas' / f'{iid}.typ')
                old = state['ideas'].get(iid, {})
                history = list(old.get('history', []))
                if not old:
                    history.append(f"{idea['created_at'][:10]}: submitted")
                if idea['status'] != old.get('status'):
                    history.append(f"{today}: {idea['status']}")
                if idea.get('ai_done_at') and idea['ai_done_at'] != old.get('ai_done_at'):
                    history.append(f"{idea['ai_done_at'][:10]}: AI processed")
                own_matches = [m for m in matches if int(iid) in (m['idea_a'], m['idea_b'])]
                connections = sorted(f"{m['idea_a']}:{m['idea_b']}:{m['kind']}" for m in own_matches if m['a_connect'] and m['b_connect'])
                for connection in connections:
                    if connection not in old.get('connections', []):
                        history.append(f'{today}: connected {connection}')
                title = idea.get('ai_title') or f'Idea {iid}'
                head = _split(path.read_text())[0] if path.exists() else build_header(
                    'idea-' + iid, title, idea['created_at'][:10], ['ideahub'], {}, kind='idea', project='Idea hub')
                # Keep a submitted note's identity and aliases; refresh Hub metadata.
                tags = sorted(parse_header(head).tags | {'ideahub'})
                for field, value in (('kind', 'idea'), ('project', 'Idea hub'), ('tags', tags)):
                    head = re.sub(r'(?m)^(\s*' + field + r':\s*)[^\n]*',
                                  lambda m: m[1] + _typst_value(value) + ',', head)
                if iid not in local or path.parent == vault / 'ideas':
                    head = re.sub(r'(?m)^(\s*title:\s*)[^\n]*',
                                  lambda m: m[1] + _typst_value(title) + ',', head)
                for k in ('status', 'ai_title', 'ai_summary', 'ai_keywords', 'ai_model', 'ai_done_at'):
                    head = _set_property(head, k, idea.get(k))
                head = _set_property(head, 'hub_id', iid)
                content = ['= ' + escape_markup(title)]
                person = people.get(idea['person_id'], {})
                if person:
                    content += ['', '== Student', '', escape_markup(person['name'])]
                for k in ('body', 'link', 'can_bring', 'looking_for', 'ai_title', 'ai_summary'):
                    if idea.get(k):
                        content += ['', '== ' + escape_markup(k), '', escape_markup(idea[k])]
                content += ['', '== Keywords', '', escape_markup(', '.join(idea.get('ai_keywords', []))), '', '== Matches']
                for m in own_matches:
                    other = str(m['idea_b'] if m['idea_a'] == int(iid) else m['idea_a'])
                    target = local.get(other)
                    ref = parse_header(_split(target.read_text())[0]).id if target else 'idea-' + other
                    content.append(f'- #tylog.ref-note({_typst_string(ref)})[{escape_markup(by_id.get(other, {}).get("ai_title") or "Idea " + other)}]; ' + escape_markup(m['kind'] + ': ' + m['reason']))
                content += ['', '== History', ''] + [f'- {date}: {escape_markup(text)}' for date, text in (h.split(': ', 1) for h in history)]
                _note(path, head, '\n'.join(content))
                state['ideas'][iid] = {'status': idea['status'], 'ai_done_at': idea.get('ai_done_at'),
                                       'connections': connections, 'history': history}
                write_job(state_path, state)
