"""V6: UTC message order, non-destructive migration and Hermes tool SSE updates."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from app import domain, persistence as db


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    path = str(tmp_path / 'v6.sqlite3')
    monkeypatch.setenv('DEMO_DATABASE_PATH', path)
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD', 'strong-demo-only-password')
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'strong-demo-only-signing-secret-with-40-chars')
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('VERCEL', raising=False)
    db._INITIALIZED.discard(path)
    db.initialize()
    return path


def auth_client():
    client = TestClient(main.app)
    login = client.post('/api/auth/login', json={'credential': 'strong-demo-only-password'})
    assert login.status_code == 200, login.text
    return client, login.json()['csrf_token']


def new_lead(client, csrf):
    response = client.post('/api/leads', headers={'X-Demo-Csrf': csrf}, json={
        'first_name': 'Alex', 'last_name': 'Test', 'email': 'alex@example.test', 'phone': '+51999999999',
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_migration_converts_mixed_timezone_history_and_preserves_order(tmp_path, monkeypatch):
    path = str(tmp_path / 'legacy.sqlite3')
    with sqlite3.connect(path) as con:
        for sql in db.SCHEMA:
            con.execute(sql)
        # This reproduces pre-v6 mixed timestamps and TEXT-sort wrong order.
        con.execute("INSERT INTO leads VALUES(?,?,?,?,?,?,?,?,?)", (
            'lead1', 'administrator', 'project1', 'Alex', 'Test', 'alex@example.test',
            '+51999999999', '2026-11-21T03:00:00Z', '2026-11-21T03:02:00Z'))
        con.execute("INSERT INTO conversations(id,lead_id,state_json,created_at,updated_at) VALUES(?,?,?,?,?)", (
            'conv1', 'lead1', '{}', '2026-11-21T03:00:00Z', '2026-11-21T03:02:00Z'))
        for id,role,created in [
            ('greeting', 'assistant', '2026-11-21T03:00:00Z'),
            ('inbound', 'user', '2026-11-20T22:01:00-05:00'),
            ('reply', 'assistant', '2026-11-20T22:01:08-05:00'),
        ]:
            con.execute('INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',
                        (id, 'conv1', role, id, created))
    monkeypatch.setenv('DEMO_DATABASE_PATH', path)
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('VERCEL', raising=False)
    db._INITIALIZED.discard(path)
    db.initialize()
    with db.database() as con:
        rows = list(con.execute('''SELECT id,created_at,sequence_number FROM messages
                                   WHERE conversation_id=? ORDER BY sequence_number''', ('conv1',)))
        assert [(r[0], r[2]) for r in rows] == [('greeting', 1), ('inbound', 2), ('reply', 3)]
        assert all(r[1].endswith('Z') for r in rows)
        assert rows[1][1] > rows[0][1]  # UTC instant, not original local text
        assert con.execute("SELECT value FROM schema_meta WHERE key='message_sequence_v1'").fetchone()
    db._INITIALIZED.discard(path)
    db.initialize()  # idempotent, no destructive rebuild
    with db.database() as con:
        assert [x[0] for x in con.execute('SELECT id FROM messages WHERE conversation_id=? ORDER BY sequence_number',
                                         ('conv1',))] == ['greeting', 'inbound', 'reply']


def test_declared_house_bedrooms_and_utc_order_are_saved(app_env):
    client, csrf = auth_client()
    lead = new_lead(client, csrf)
    auth = db.session_lookup(client.cookies.get(main.COOKIE))
    state = lead['state']
    message = "I'm looking for a 2-bedroom house"
    domain.begin_turn(state, message)
    assert domain.verify_explicit_preferences(state, message)['ok']
    assert state['lead']['property_type'] == 'House'
    assert state['lead']['min_bedrooms'] == 2
    assert state['ui_fact_evidence']['property_type']['source_message_id'] == state['_active_source_message_id']
    domain.end_turn(state, 'What is your budget?')
    updated = db.save_turn(auth, lead['id'], 0, state, [], message, 'What is your budget?')
    assert [m['sequence_number'] for m in updated['messages']] == [1, 2, 3]
    assert [m['role'] for m in updated['messages']] == ['assistant', 'user', 'assistant']
    assert all(m['created_at'].endswith('Z') for m in updated['messages'])
    assert updated['state']['lead']['min_bedrooms'] == 2
    assert updated['state']['ui_fact_evidence']['property_type']['source_message_id'] == updated['messages'][1]['id']
    canonical = updated['state']['bp_episode']['leads']['lead_profile_data']['sales_profile']['current']
    assert canonical['bedrooms']['source_ref']['message_id'] == updated['messages'][1]['id']


@pytest.mark.parametrize('message', [
    "I don't want a 2-bedroom house",
    'Do you have 2-bedroom houses?',
    "I'm looking for an apartment or house",
    'I need a 2-bedroom house but maybe an apartment',
    'I wonder whether a 2-bedroom house would be enough?',
])
def test_ambiguous_and_question_messages_not_silently_classified(message):
    assert domain.explicitly_declared_preferences(message) == {}


def test_sse_emits_profile_updates_but_only_commits_on_success(app_env, monkeypatch):
    client, csrf = auth_client()
    lead = new_lead(client, csrf)
    def fake_chat(message, history, state, *, extra_occupied=None, on_tool_state=None):
        assert state['lead']['property_type'] == 'House'  # explicit validation before Hermes
        assert state['lead']['min_bedrooms'] == 2
        domain.update_lead(state, purchase_timeframe='within 3 months')
        if on_tool_state:
            on_tool_state({'tool': 'update_lead_profile', 'ok': True,
                           'profile': domain.profile_snapshot(state)})
        return {'reply': 'What is your budget?', 'history': [
            {'role': 'user', 'content': message}, {'role': 'assistant', 'content': 'What is your budget?'}]}
    monkeypatch.setattr(main, 'chat', fake_chat)
    with client.stream('POST', f"/api/leads/{lead['id']}/chat/stream",
                       headers={'X-Demo-Csrf': csrf}, json={'message': "I'm looking for a 2-bedroom house"}) as response:
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        frames = ''.join(response.iter_text()).split('\n\n')
    names = []
    values = {}
    for frame in frames:
        if not frame.startswith('event: '):
            continue
        name, payload = frame.split('\ndata: ', 1)
        name = name.removeprefix('event: ')
        names.append(name)
        values.setdefault(name, []).append(json.loads(payload))
    assert names[0:2] == ['received', 'profile_preview']
    assert names[-1] == 'committed'
    assert names.count('profile_preview') == 2
    assert values['profile_preview'][0]['profile']['lead']['min_bedrooms'] == 2
    assert values['profile_preview'][1]['profile']['lead']['purchase_timeframe'] == 'within 3 months'
    assert values['committed'][0]['lead']['revision'] == 1
    assert values['committed'][0]['lead']['messages'][0]['role'] == 'assistant'

    def broken_chat(*_args, **_kwargs):
        raise RuntimeError('Model unavailable')
    monkeypatch.setattr(main, 'chat', broken_chat)
    with client.stream('POST', f"/api/leads/{lead['id']}/chat/stream",
                       headers={'X-Demo-Csrf': csrf}, json={'message': 'I prefer an apartment with 3 bedrooms'}) as response:
        result = ''.join(response.iter_text())
    assert 'event: profile_preview' in result and 'event: error' in result
    saved = client.get(f"/api/leads/{lead['id']}").json()
    assert saved['revision'] == 1
    assert saved['state']['lead']['property_type'] == 'House'
    assert saved['state']['lead']['min_bedrooms'] == 2
    assert len(saved['messages']) == 3


def test_stream_requires_admin_csrf(app_env):
    client, csrf = auth_client()
    lead = new_lead(client, csrf)
    assert client.post(f"/api/leads/{lead['id']}/chat/stream", json={'message': 'Hello'}).status_code == 403
    assert TestClient(main.app).post(f"/api/leads/{lead['id']}/chat/stream",
                                     json={'message': 'Hello'}).status_code == 401


def test_hermes_contact_updates_master_lead_and_provenance(app_env):
    client, csrf = auth_client()
    lead = new_lead(client, csrf)
    auth = db.session_lookup(client.cookies.get(main.COOKIE))
    state = lead['state']
    domain.begin_turn(state, 'Please use my new email alex2@example.test')
    response = domain.update_lead(state, email='alex2@example.test', phone='+51988888888')
    assert response['ok']
    domain.end_turn(state, 'Contact details updated')
    updated = db.save_turn(auth, lead['id'], 0, state, [],
        'Please use my new email alex2@example.test', 'Contact details updated')
    assert updated['email'] == 'alex2@example.test'
    assert updated['phone'] == '+51988888888'
    evidence = updated['state']['ui_fact_evidence']
    assert evidence['email']['source_message_id'] == updated['messages'][1]['id']
    assert updated['messages'][1]['sequence_number'] == 2


def test_corrected_preference_keeps_superseded_evidence(app_env):
    client, csrf = auth_client()
    lead = new_lead(client, csrf)
    auth = db.session_lookup(client.cookies.get(main.COOKIE))
    state = lead['state']
    for turn, message in enumerate(["I'm looking for a 2-bedroom house",
                                    "I need a house with 3 bedrooms"], start=0):
        domain.begin_turn(state, message)
        assert domain.verify_explicit_preferences(state, message)['ok']
        domain.end_turn(state, 'Understood')
        lead = db.save_turn(auth, lead['id'], turn, state, [], message, 'Understood')
    assert lead['state']['lead']['min_bedrooms'] == 3
    sales = lead['state']['bp_episode']['leads']['lead_profile_data']['sales_profile']
    assert sales['current']['bedrooms']['value'] == 3
    assert any(f['field'] == 'bedrooms' and f['value'] == 2 for f in sales['history'])
    assert sales['current']['bedrooms']['source_ref']['message_id'] == lead['messages'][3]['id']
    assert [m['sequence_number'] for m in lead['messages']] == [1, 2, 3, 4, 5]
