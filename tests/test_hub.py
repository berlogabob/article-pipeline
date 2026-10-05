import json
from pathlib import Path

import httpx
import pytest

from article_pipeline.config import Config
from article_pipeline.hub import sync_hub, _split, _property, _set_property
from article_pipeline.stage3_render_typst import build_header
from article_pipeline import worker


@pytest.fixture(params=['password', 'service'])
def hub(tmp_path, request):
    cfg = Config()
    cfg.typst.vault_dir = str(tmp_path / 'vault')
    env = tmp_path / 'credentials'
    env.write_text('SUPABASE_URL=https://hub.test\nSUPABASE_ANON_KEY=secret-key\nSTAFF_EMAIL=me@test.org\nSTAFF_PASSWORD="secret password"\n')
    service = request.param == 'service'
    if service:
        env.write_text('SUPABASE_URL=https://hub.test\nSUPABASE_SERVICE_KEY=service-secret\nSTAFF_EMAIL=me@test.org\n')
    staff = {'id': 7, 'name': 'My Name', 'email': 'me@test.org', 'is_staff': True, 'auth_user_id': 'user'}
    idea = {'id': 1, 'person_id': 8, 'body': 'Original #idea [text]', 'link': 'https://test.org',
            'can_bring': 'tools', 'looking_for': 'help', 'status': 'new', 'created_at': '2026-10-01T12:00:00Z',
            'ai_title': None, 'ai_summary': None, 'ai_keywords': [], 'ai_done_at': None}
    tables = {'people': [staff, {'id': 8, 'name': 'Student'}], 'ideas': [idea, {**idea, 'id': 2}],
              'activities': [{'id': 4, 'kind': 'consultation', 'requester_id': 8, 'owner_staff_id': 7,
                              'starts_at': '2026-10-05T12:00:00Z', 'ends_at': '2026-10-05T12:30:00Z',
                              'status': 'requested', 'purpose': 'Project [one]'}],
              'idea_matches': [{'idea_a': 1, 'idea_b': 2, 'kind': 'similar', 'reason': 'Shared #topic',
                                'score': .9, 'a_connect': False, 'b_connect': False}]}
    seen = []
    def handler(req):
        seen.append(req)
        assert req.headers['apikey'] == ('service-secret' if service else 'secret-key')
        if req.url.path == '/auth/v1/token':
            assert not service
            assert json.loads(req.content) == {'email': 'me@test.org', 'password': 'secret password'}
            assert req.url.params['grant_type'] == 'password'
            return httpx.Response(200, json={'access_token': 'secret-jwt', 'user': {'id': 'user'}})
        assert req.headers['Authorization'] == ('Bearer service-secret' if service else 'Bearer secret-jwt')
        if req.url.path.endswith('/rpc/submit_idea'):
            data = json.loads(req.content)
            assert data['p_name'] == staff['name'] and data['p_email'] == staff['email']
            assert data['p_body'] == 'My original idea text'
            tables['ideas'].append({**idea, 'id': 3, 'status_token': 'private-token'})
            return httpx.Response(200, json='private-token')
        table = req.url.path.rsplit('/', 1)[1]
        if req.method == 'PATCH':
            row = next(i for i in tables[table] if str(i['id']) == req.url.params['id'][3:])
            row.update(json.loads(req.content))
            return httpx.Response(200, json=[row])
        result = tables[table]
        for field in ('auth_user_id', 'email', 'status_token', 'kind', 'owner_staff_id', 'is_staff'):
            if field in req.url.params:
                result = [r for r in result if str(r.get(field)).lower() == req.url.params[field][3:]]
        if table == 'people' and 'is_staff' in req.url.params:
            assert req.url.params['is_staff'] == 'eq.true'
            assert req.url.params.get('email' if service else 'auth_user_id') == ('eq.me@test.org' if service else 'eq.user')
        if table == 'activities':
            assert req.url.params['kind'] == 'eq.consultation'
            assert req.url.params['owner_staff_id'] == 'eq.7'
        offset = int(req.url.params['offset'])
        return httpx.Response(200, json=result[offset:offset + 1000])
    def poll():
        sync_hub(cfg, env_path=env, transport=httpx.MockTransport(handler))
    return Path(cfg.typst.vault_dir), tables, seen, poll, cfg, env


def test_import_idempotence_preservation_and_status_roundtrip(hub):
    vault, tables, seen, poll, _, _ = hub
    poll()
    idea = vault / 'ideas/1.typ'
    consult = vault / 'events/2026/10/consult-4.typ'
    assert 'event_type": "consultation"' in consult.read_text()
    assert 'start": "13:00"' in consult.read_text()
    assert '#tylog.ref-note("idea-2")' in idea.read_text()
    assert r'Original \#idea \[text\]' in idea.read_text()
    before = idea.read_text()
    poll()
    assert idea.read_text() == before
    head, body = _split(before)
    idea.write_text(_set_property(head, 'status', 'approved') + body + '\nMy handwritten notes\n')
    consult.write_text(consult.read_text() + '\nConsultation notes\n')
    tables['activities'][0].update(starts_at='2026-11-05T12:00:00Z', status='cancelled')
    tables['ideas'][0].update(ai_title='AI title', ai_summary='AI #summary', ai_keywords=['tools'], ai_done_at='2026-10-05T10:00:00Z')
    poll()
    assert tables['ideas'][0]['status'] == 'approved'
    assert 'My handwritten notes' in idea.read_text()
    assert 'AI processed' in idea.read_text() and ': approved' in idea.read_text()
    assert 'Consultation notes' in consult.read_text() and 'cancelled' in consult.read_text()
    assert len(list((vault / 'events').rglob('consult-4.typ'))) == 1
    tables['ideas'][0]['status'] = 'archived'
    tables['idea_matches'][0].update(a_connect=True, b_connect=True)
    poll()
    assert _property(_split(idea.read_text())[0], 'status') == 'archived'
    assert ': connected ' in idea.read_text()
    before = idea.read_text()
    poll()
    assert idea.read_text() == before
    assert len([r for r in seen if r.method == 'PATCH']) == 1


def test_history_dates_and_existing_managed_block_rewrite(hub):
    vault, _, _, poll, _, _ = hub
    poll()
    idea = vault / 'ideas/1.typ'
    assert '- 2026-10-01: submitted' in idea.read_text()
    idea.write_text(idea.read_text().replace('2026-10-01: submitted', r'2026\-10\-01: submitted')
                    + '\nMy handwritten notes\n')
    state_path = vault / '_system/hub-sync.json'
    state = json.loads(state_path.read_text())
    state['ideas']['1']['history'].append('2026-10-02: connected #topic: [one-two]')
    state_path.write_text(json.dumps(state))
    poll()
    text = idea.read_text()
    assert '- 2026-10-01: submitted' in text
    assert r'2026\-10\-01: submitted' not in text
    assert r'- 2026-10-02: connected \#topic: \[one\-two\]' in text
    assert text.endswith('\nMy handwritten notes\n')
    poll()
    assert idea.read_text() == text


@pytest.mark.parametrize('kind,tags', [('idea', []), ('note', ['#idea'])])
def test_submit_as_me_and_reuse_local_note(hub, kind, tags):
    vault, _, seen, poll, _, _ = hub
    vault.mkdir()
    note = vault / 'mine.typ'
    note.write_text(build_header('mine', 'My idea', '2026-10-05', tags, {}, kind=kind) + '\nMy original idea text')
    poll()
    assert _property(_split(note.read_text())[0], 'hub_id') == '3'
    assert 'My original idea text' in note.read_text()
    assert not (vault / 'ideas/3.typ').exists()
    poll()
    assert len([r for r in seen if r.url.path.endswith('/submit_idea')]) == 1
    assert '#tylog.ref-note("idea-2")' in (vault / 'ideas/1.typ').read_text()


def test_failures_sanitized_and_no_writes(hub, caplog):
    vault, _, _, _, cfg, env = hub
    def fail(req):
        return httpx.Response(401, json={'error': 'secret password secret-jwt service-secret'})
    with pytest.raises(RuntimeError, match=r'^Hub sync failed \(HTTPStatusError: HTTP 401\)$') as err:
        sync_hub(cfg, env_path=env, transport=httpx.MockTransport(fail))
    assert err.value.__suppress_context__
    assert 'secret' not in caplog.text
    assert 'secret' not in str(err.value)
    assert not list(vault.rglob('*.typ'))


def test_interrupted_submission_recovers_without_resubmitting(hub, monkeypatch):
    from article_pipeline import hub as module
    vault, tables, seen, poll, _, _ = hub
    vault.mkdir()
    note = vault / 'mine.typ'
    note.write_text(build_header('mine', 'My idea', None, [], {}, kind='idea') + '\nMy original idea text')
    original = module._write
    def interrupted(path, text):
        if path == note:
            raise OSError('interrupted')
        original(path, text)
    monkeypatch.setattr(module, '_write', interrupted)
    with pytest.raises(RuntimeError, match='Hub sync failed'):
        poll()
    monkeypatch.setattr(module, '_write', original)
    poll()
    assert _property(_split(note.read_text())[0], 'hub_id') == '3'
    assert len([r for r in seen if r.url.path.endswith('/submit_idea')]) == 1
    tables['idea_matches'].append({**tables['idea_matches'][0], 'idea_b': 3})
    poll()
    assert '#tylog.ref-note("mine")' in (vault / 'ideas/1.typ').read_text()


def test_paginated_people_and_nonstaff_rejected(hub):
    _, tables, seen, poll, _, _ = hub
    tables['people'].extend({'id': i + 100, 'name': 'Other'} for i in range(1000))
    poll()
    assert any(r.url.path.endswith('/people') and r.url.params.get('offset') == '1000' for r in seen)
    tables['people'][0]['is_staff'] = False
    with pytest.raises(RuntimeError, match='Hub sync failed'):
        poll()


def test_consultations_scoped_to_me_and_all_ideas_visible(hub):
    vault, tables, seen, poll, _, _ = hub
    tables['activities'].extend([
        {**tables['activities'][0], 'id': 5, 'owner_staff_id': 9},
        {**tables['activities'][0], 'id': 6, 'kind': 'other'},
    ])
    poll()
    assert [p.name for p in (vault / 'events').rglob('*.typ')] == ['consult-4.typ']
    assert {p.name for p in (vault / 'ideas').glob('*.typ')} == {'1.typ', '2.typ'}
    assert all(set(r.url.params) == {'select', 'order', 'limit', 'offset'}
               for r in seen if r.url.path == '/rest/v1/ideas')


def test_service_key_preferred_with_password_credentials(hub):
    _, _, seen, poll, _, env = hub
    if 'SUPABASE_SERVICE_KEY=' not in env.read_text():
        pytest.skip('Service-key precedence applies to service authentication')
    with env.open('a') as stream:
        stream.write('SUPABASE_ANON_KEY=unused-key\nSTAFF_PASSWORD=unused-password\n')
    poll()
    assert not any(r.url.path == '/auth/v1/token' for r in seen)


@pytest.mark.parametrize('identity_error', ['missing', 'duplicate'])
def test_staff_identity_must_be_unique_before_sync(hub, identity_error):
    vault, tables, seen, poll, _, _ = hub
    if identity_error == 'missing':
        tables['people'][0].update(email='other@test.org', auth_user_id='other-user')
    else:
        tables['people'].append({**tables['people'][0], 'id': 9})
    with pytest.raises(RuntimeError, match=r'^Hub sync failed \(ValueError: Staff identity unavailable\)$'):
        poll()
    assert not list(vault.rglob('*.typ'))
    assert all(r.url.path in ('/auth/v1/token', '/rest/v1/people') for r in seen)


def test_cli_sync_and_watch_schedule(tmp_path, monkeypatch):
    cfg = Config()
    cfg.typst.vault_dir = str(tmp_path)
    monkeypatch.setattr(worker, 'load_config', lambda root: cfg)
    polls, drains = [], []
    monkeypatch.setattr(worker, 'sync_hub', lambda cfg: polls.append(True))
    monkeypatch.setattr(worker, 'drain_jobs', lambda cfg: (drains.append(True) or (0, 0)))
    assert worker.main(['--sync-hub']) == 0
    assert len(polls) == 1 and not drains
    ticks = iter([0, 0, 5, 599, 600, 600])
    monkeypatch.setattr(worker.time, 'monotonic', lambda: next(ticks))
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 4:
            raise KeyboardInterrupt
    monkeypatch.setattr(worker.time, 'sleep', sleep)
    assert worker.main(['--watch']) == 0
    assert len(polls) == 3 and len(drains) == 4


def test_scan_skips_unparseable_notes_and_ignores_nonideas(hub, caplog):
    vault, _, seen, poll, _, _ = hub
    vault.mkdir()
    notes = {
        'missing-properties.typ': '#show: tylog.note.with(\n id: "broken",\n title: "Broken idea",\n kind: "idea",\n tags: (),\n)\nUntouched',
        'unclosed.typ': '#show: tylog.note.with(\n id: "broken",\n title: "Broken",',
        'missing-title.typ': '#show: tylog.note.with(\n id: "broken",\n kind: "idea",\n)',
        'ordinary.typ': '#show: tylog.note.with(\n id: "ordinary",\n title: "Ordinary",\n kind: "note",\n tags: (),\n)',
        'nonidea-hub-id.typ': build_header('other', 'Other', None, [], {'hub_id': '1'}, kind='note'),
        'valid.typ': build_header('mine', 'My idea', None, ['#idea'], {}, kind='note') + '\nMy original idea text',
    }
    for name, text in notes.items():
        (vault / name).write_text(text)
    with caplog.at_level('INFO', logger='article_pipeline'):
        poll()
    assert 'Hub vault scan skipped 3 unparseable notes' in caplog.text
    for name, text in notes.items():
        if name != 'valid.typ':
            assert (vault / name).read_text() == text
    assert _property(_split((vault / 'valid.typ').read_text())[0], 'hub_id') == '3'
    assert len([r for r in seen if r.url.path.endswith('/submit_idea')]) == 1


def test_failure_reason_does_not_expose_arbitrary_exception_text(hub):
    _, _, _, _, cfg, env = hub
    def fail(req):
        raise ValueError('secret password secret-jwt service-secret')
    with pytest.raises(RuntimeError, match=r'^Hub sync failed \(ValueError: invalid data\)$') as err:
        sync_hub(cfg, env_path=env, transport=httpx.MockTransport(fail))
    assert 'secret' not in str(err.value)
