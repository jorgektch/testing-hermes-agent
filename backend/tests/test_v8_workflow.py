"""V8: sale-only site visits, SMS presentation and durable Turso identity."""
from datetime import timedelta

import pytest

from app import domain, persistence as db, scheduling, sms

AUTH = {'role': 'administrator', 'owner_id': 'administrator'}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMO_DATABASE_PATH', str(tmp_path / 'shared-v8.sqlite3'))
    for name in ('TURSO_DATABASE_URL', 'DEMO_PERSISTENT_TURSO_URL',
                 'DEMO_PERSISTENT_TURSO_TOKEN', 'VERCEL'):
        monkeypatch.delenv(name, raising=False)
    db.initialize()
    yield


def next_monday():
    day = scheduling.local_now().date() + timedelta(days=1)
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day


def offer_one(lead, day, start='13:00', include_occupancy=False):
    state = lead['state']
    domain.begin_turn(state, 'I want to see H-201 next week.')
    assert domain.track_property_interest(state, 'H-201', 'interested')['ok']
    offer = domain.slots(state, day.isoformat(),
                         extra_occupied=db.occupied_by_advisor() if include_occupancy else None)
    assert offer['ok'] and any(x['start_time'] == start for x in offer['available_slots']), offer
    assert not any(name in str(offer) for name in ('Olivia', 'Daniel', 'Sophia', 'ADV-01'))
    domain.end_turn(state, 'Which exact hourly start works for you?')
    lead = db.save_turn(AUTH, lead['id'], lead['revision'], state, [],
                        'I want to see H-201 next week.', 'Which exact hourly start works for you?')
    return lead, offer, state


def select_and_book(lead, offer, state, start='13:00', *, confirm=True,
                    occupied=None):
    day = offer['date']
    text = f'I choose {start} for H-201.'
    domain.begin_turn(state, text)
    result = domain.select_visit_time(state, offer['proposal_id'], day, start)
    assert result['ok'], result
    domain.end_turn(state, 'For H-201 on the chosen day and hour, reply Yes, I confirm. Confirm?')
    lead = db.save_turn(AUTH, lead['id'], lead['revision'], state, [],
                        text, 'For H-201 on the chosen day and hour, reply Yes, I confirm. Confirm?')
    assert not state['booked_visits']
    if not confirm:
        return lead, state, None
    domain.begin_turn(state, 'Yes, I confirm')
    pending = domain.book_visit(state, lead['first_name'], lead['last_name'],
                                lead['email'], lead['phone'],
                                date_str=day, start_time=start,
                                proposal_id=offer['proposal_id'], unit_id='H-201',
                                extra_occupied=occupied)
    assert pending['ok'] and pending['pending_commit'], pending
    assert 'advisor_name' not in pending
    assert state['booked_visits'][-1]['advisor_name'] is None
    domain.end_turn(state, 'I have submitted your request.')
    return lead, state, pending


def test_no_unselected_unit_no_vague_date_no_precommit_name():
    state = domain.blank_state()
    assert 'error' in domain.slots(state, '2026-12-01')
    domain.begin_turn(state, 'Maybe a home visit next week')
    assert 'error' in domain.track_property_interest(state, 'H-201', 'interested')
    assert 'error' in domain.update_lead(state, interest_unit_id='H-201')
    domain.begin_turn(state, 'I want to visit H-201.')
    assert domain.track_property_interest(state, 'H-201', 'interested')['ok']
    broad=domain.slots(state, 'next week')
    assert broad.get('ok') and broad['available_dates']
    assert broad['proposal_id'] is None
    upcoming=domain.slots(state)
    assert upcoming.get('ok') and upcoming['available_dates']
    offer = domain.slots(state, next_monday().isoformat())
    assert offer['ok'] and offer['unit_id'] == 'H-201'
    assert all(slot['start_time'].endswith(':00') for slot in offer['available_slots'])
    assert all('advisor' not in str(slot).lower() for slot in offer['available_slots'])
    assert offer['time_ranges']
    assert all('start_times' in group for group in offer['time_ranges'])


def test_lead_must_choose_specific_hour_then_confirm_next_turn():
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201')
    assert domain.track_property_interest(state, 'H-201', 'interested')['ok']
    offer = domain.slots(state, next_monday().isoformat())
    assert offer['ok']
    slot = offer['available_slots'][0]
    # A silent hour invented by Hermes is not a lead choice.
    assert 'error' in domain.select_visit_time(state, offer['proposal_id'], slot['date'], slot['start_time'])
    domain.begin_turn(state, f'{slot["start_time"]} works for me')
    assert domain.select_visit_time(state, offer['proposal_id'], slot['date'], slot['start_time'])['ok']
    assert 'error' in domain.book_visit(state, 'Test', 'Buyer', 'test@example.test', '+51999999999',
        date_str=slot['date'], start_time=slot['start_time'], proposal_id=offer['proposal_id'], unit_id='H-201')
    domain.end_turn(state, 'For H-201 on that date and hour, reply Yes, I confirm. Confirm?')
    domain.begin_turn(state, 'Yes, I confirm')
    assert 'error' in domain.book_visit(state, 'Test', 'Buyer', 'test@example.test', '+51999999999',
        date_str=slot['date'], start_time='09:30', proposal_id=offer['proposal_id'], unit_id='H-201')
    assert 'error' in domain.book_visit(state, 'Test', 'Buyer', 'test@example.test', '+51999999999',
        date_str=slot['date'], start_time=slot['start_time'], proposal_id=offer['proposal_id'], unit_id='H-104')
    assert domain.book_visit(state, 'Test', 'Buyer', 'test@example.test', '+51999999999',
        date_str=slot['date'], start_time=slot['start_time'], proposal_id=offer['proposal_id'], unit_id='H-201')['pending_commit']


def test_round_robin_3_and_no_fourth_double_booking(store):
    booked = []
    day = next_monday()
    for first in ('Alice', 'Brenda', 'Claire'):
        lead = db.create_lead(AUTH, first, 'Demo', f'{first.lower()}@example.test', '+51999999999')
        lead, offer, state = offer_one(lead, day)
        lead, state, pending = select_and_book(lead, offer, state)
        saved = db.save_turn(AUTH, lead['id'], lead['revision'], state, [],
                             'Yes, I confirm', 'I have submitted your request.')
        visit = saved['state']['booked_visits'][-1]
        assert visit['status'] == 'confirmed' and visit['unit_id'] == 'H-201'
        assert visit['advisor_name'] and visit['advisor_id']
        assert visit['visit_location']['maps_url'] is None
        assert visit['visit_location']['verification_status'] == 'synthetic'
        assert visit['advisor_name'] in ''.join(m['content'] for m in saved['messages'][-3:])
        booked.append(visit)
    assert len({v['advisor_id'] for v in booked}) == 3
    with db.database() as con:
        occupancy = con.execute('SELECT COUNT(*) FROM meeting_occupancy WHERE meeting_id IN (?,?,?)',
                                tuple(v['meeting_id'] for v in booked)).fetchone()[0]
        assert occupancy == 12  # 3 visits × 4 15-minute buckets
        cursor = con.execute('SELECT next_index FROM advisor_rotation').fetchone()[0]
        assert cursor == 0  # Completed a full cycle
    fourth = db.create_lead(AUTH, 'David', 'Demo', 'david@example.test', '+51999999999')
    fourth, offer, state = offer_one(fourth, day)
    fourth, state, _ = select_and_book(fourth, offer, state)
    with pytest.raises(db.Conflict, match='no longer available|became unavailable'):
        db.save_turn(AUTH, fourth['id'], fourth['revision'], state, [],
                     'Yes, I confirm', 'I have submitted your request.')
    with db.database() as con:
        assert con.execute('SELECT COUNT(*) FROM meetings WHERE lead_id=?', (fourth['id'],)).fetchone()[0] == 0


def test_database_identity_stable_with_reinitialize(store):
    before = db.storage_diagnostics()
    assert before['counts']['leads'] == 0
    db.create_lead(AUTH, 'Test', 'Person', 'a@example.test', '+51999999999')
    db.initialize()
    after = db.storage_diagnostics()
    assert before['database_fingerprint'] == after['database_fingerprint']
    assert after['counts']['leads'] == 1
    assert after['counts']['conversations'] == 1
    assert after['counts']['messages'] == 1
    assert after['counts']['meetings'] == before['counts']['meetings']


def test_pinned_turso_credentials_are_paired(monkeypatch):
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://marketplace.example')
    monkeypatch.setenv('TURSO_DATABASE_TURSO_AUTH_TOKEN', 'marketplace-token')
    monkeypatch.setenv('DEMO_PERSISTENT_TURSO_URL', 'libsql://main.example')
    monkeypatch.delenv('DEMO_PERSISTENT_TURSO_TOKEN', raising=False)
    with pytest.raises(RuntimeError, match='BOTH'):
        db._remote_credentials()
    monkeypatch.setenv('DEMO_PERSISTENT_TURSO_TOKEN', 'main-token')
    assert db._remote_credentials() == ('libsql://main.example', 'main-token')
    monkeypatch.delenv('DEMO_PERSISTENT_TURSO_URL')
    monkeypatch.delenv('DEMO_PERSISTENT_TURSO_TOKEN')
    assert db._remote_credentials() == ('libsql://marketplace.example', 'marketplace-token')


def test_sms_plain_text_link_segments_and_unicode():
    original = ('**View H-201** [Gallery](https://testing-hermes-agent-alpha.vercel.app/properties/H-201/gallery) '
                'for all available synthetic images.')
    sms_segments = sms.outgoing_segments(original)
    assert sms_segments and all('**' not in text and '](' not in text for text in sms_segments)
    assert 'https://testing-hermes-agent-alpha.vercel.app/properties/H-201/gallery' in ' '.join(sms_segments)
    assert all(sms.details(segment)['segments'] == 1 for segment in sms_segments)
    assert sms.outgoing_segments('Hello!') == ['Hello!']
    assert sms.details('a' * 161)['segments'] == 2
    assert sms.details('🏡')['encoding'] == 'UCS-2'
    assert sms.details('🏡')['units'] == 2
    assert len(sms.outgoing_segments('🏡' * 45)) == 1  # two carrier segments, one receiver-visible message
    assert sms.details(sms.outgoing_segments('🏡' * 45)[0])['segments'] == 2
    # Long URLs remain intact as one receiver-visible concatenated SMS.
    long_link = 'https://' + 'a' * 180 + '.example'
    assert sms.outgoing_segments(long_link) == [long_link]
    assert sms.details(long_link)['segments'] == 2


def test_unconfirmed_advisor_names_are_redacted_until_saved():
    state = domain.blank_state()
    text = 'Olivia Bennett or Daniel Rivera could visit. Advisor ADV-03 is also available.'
    clean = domain.redact_unconfirmed_advisor_names(text, state)
    assert 'Olivia Bennett' not in clean and 'Daniel Rivera' not in clean and 'ADV-03' not in clean
    state['booked_visits'].append({'status':'confirmed','advisor_name':'Olivia Bennett'})
    assert 'Olivia Bennett' in domain.redact_unconfirmed_advisor_names(text, state)
    assert 'Daniel Rivera' not in domain.redact_unconfirmed_advisor_names(text, state)


def test_storage_diagnostic_endpoint_requires_admin_and_never_exposes_url(store, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'temporary-test-signing-secret-with-over-32-characters')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD', 'temporary-very-strong-admin-password')
    client = TestClient(main.app)
    assert client.get('/api/admin/storage').status_code == 401
    login = client.post('/api/auth/login', json={'credential':'temporary-very-strong-admin-password'})
    assert login.status_code == 200
    result = client.get('/api/admin/storage')
    assert result.status_code == 200
    data = result.json()
    assert data['counts']['leads'] == 0
    assert len(data['database_fingerprint']) == 16
    assert 'TURSO' not in str(data)
    assert 'token' not in str(data).lower()
    assert login.json()['max_turns'] == main.MAX_TURNS
