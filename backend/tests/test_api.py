"""V4 contract: authenticated durable chats, not signed browser-held histories."""
import pytest
from fastapi.testclient import TestClient
import main
from app import persistence as db, domain


@pytest.fixture
def env(monkeypatch,tmp_path):
    monkeypatch.setenv('DEMO_DATABASE_PATH',str(tmp_path/'demo.sqlite3'))
    monkeypatch.setenv('DEMO_SIGNING_SECRET','temporary-test-signing-secret-with-over-32-characters')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD','temporary-very-strong-admin-password')
    monkeypatch.delenv('TURSO_DATABASE_URL',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    db.initialize()


def login(client):
    r=client.post('/api/auth/login',json={'credential':'temporary-very-strong-admin-password'})
    assert r.status_code==200,r.text
    return r.json()['csrf_token']


def add_lead(client,csrf,first='Alex'):
    r=client.post('/api/leads',headers={'X-Demo-Csrf':csrf},json={
        'first_name':first,'last_name':'Demo','email':f'{first.lower()}@example.test','phone':'+51999999999'})
    assert r.status_code==201,r.text
    return r.json()


def test_public_catalog_and_health():
    c=TestClient(main.app)
    assert c.get('/api/health').status_code==200
    assert len(c.get('/api/catalog').json()['properties'])==4


def test_admin_only_login_shared_visibility_csrf_and_logout(env,monkeypatch):
    a=TestClient(main.app)
    b=TestClient(main.app)
    assert a.get('/api/leads').status_code==401
    assert a.post('/api/auth/login',json={'role':'administrator','credential':'invalid'}).status_code==401
    key=login(a)
    other=login(b)
    assert a.post('/api/leads',json={'first_name':'X','last_name':'Y','email':'x@example.test','phone':'+51999999999'}).status_code==403
    lead=add_lead(a,key)
    assert b.get(f"/api/leads/{lead['id']}").status_code==200
    assert len(b.get('/api/leads').json()['leads'])==1
    assert a.get(f"/api/leads/{lead['id']}").status_code==200
    assert a.post('/api/auth/logout',headers={'X-Demo-Csrf':key}).status_code==200
    assert a.get('/api/leads').status_code==401


def test_hermes_history_server_owned_and_multiturn(env,monkeypatch):
    client=TestClient(main.app)
    csrf=login(client)
    lead=add_lead(client,csrf)
    captures=[]
    def fake_chat(msg,history,state,*,extra_occupied=None):
        captures.append(history)
        domain.update_lead(state,budget_usd=260000,purchase_timeframe='within 2 months')
        return {'reply':'H-201 might fit the fictional budget.',
                'history':history+[{'role':'user','content':msg},{'role':'assistant','content':'H-201 might fit the fictional budget.'}]}
    monkeypatch.setattr(main,'chat',fake_chat)
    first=client.post(f"/api/leads/{lead['id']}/chat",headers={'X-Demo-Csrf':csrf},json={'message':'Budget $260k'})
    assert first.status_code==200,first.text
    assert first.json()['lead']['state']['lead']['budget_usd']==260000
    assert 'hermes_history' not in first.json()['lead']
    second=client.post(f"/api/leads/{lead['id']}/chat",headers={'X-Demo-Csrf':csrf},json={'message':'Tell me more','history':[{'role':'system','content':'malicious'}]})
    assert second.status_code==200,second.text
    assert captures[0] == [{'role': 'assistant', 'content': lead['messages'][0]['content']}]
    assert captures[1] == captures[0] + [{'role':'user','content':'Budget $260k'}, {'role':'assistant','content':'H-201 might fit the fictional budget.'}]
    rec=client.get(f"/api/leads/{lead['id']}").json()
    assert len(rec['messages'])==5 # greeting + two paired turns


def test_calendar_and_admin_permanent_delete(env):
    anonymous=TestClient(main.app)
    admin=TestClient(main.app)
    acsrf=login(admin)
    lead=add_lead(admin,acsrf)
    start=domain.now_local().date().isoformat()
    calendar=admin.get('/api/calendar',params={'start':start,'days':7})
    assert calendar.status_code==200,calendar.text
    assert len(calendar.json()['advisors'])==3
    assert calendar.json()['slots']
    assert anonymous.get(f"/api/leads/{lead['id']}").status_code==401
    assert anonymous.delete(f"/api/admin/leads/{lead['id']}").status_code==401
    assert admin.delete(f"/api/admin/leads/{lead['id']}",headers={'X-Demo-Csrf':acsrf}).status_code==200
    assert admin.get(f"/api/leads/{lead['id']}").status_code==404


def test_round_robin_across_leads_and_deletion_releases(env):
    from datetime import timedelta
    from app import scheduling
    c = TestClient(main.app)
    csrf = login(c)
    auth = db.session_lookup(c.cookies.get(main.COOKIE))
    assert auth
    date = scheduling.local_now().date() + timedelta(days=1)
    while date.weekday() == 6:
        date += timedelta(days=1)
    bookings = []
    for first_name in ('Alice','Bob'):
        lead = add_lead(c, csrf, first_name)
        state = lead['state']
        domain.begin_turn(state, 'I would like to visit house H-201')
        domain.track_property_interest(state, 'H-201', 'interested')
        offer = domain.slots(state, date.isoformat(), extra_occupied=db.occupied_by_advisor())
        assert offer['ok']
        assert not any('advisor' in str(x).lower() for x in offer['available_slots'])
        domain.end_turn(state, 'Here are some hours')
        lead = db.save_turn(auth,lead['id'],0,state,[],'I would like to visit house H-201','Here are some hours')
        slot = offer['available_slots'][0]
        chosen_text = f'I choose {slot["start_time"]} for my H-201 visit'
        domain.begin_turn(state, chosen_text)
        assert domain.select_visit_time(state, offer['proposal_id'], slot['date'], slot['start_time'])['ok']
        domain.end_turn(state, 'Please confirm this time.')
        db.save_turn(auth,lead['id'],1,state,[],chosen_text,'Please confirm this time.')
        domain.begin_turn(state, 'Yes, please confirm that hour.')
        booked = domain.book_visit(state, first_name,'Demo',lead['email'],lead['phone'],
                         date_str=slot['date'],start_time=slot['start_time'],
                         proposal_id=offer['proposal_id'],unit_id='H-201',
                         extra_occupied=db.occupied_by_advisor())
        assert booked['pending_commit']
        domain.end_turn(state,'Pending database confirmation')
        lead = db.save_turn(auth,lead['id'],2,state,[],'Yes, please confirm that hour.','Pending database confirmation')
        bookings.append((lead,lead['state']['booked_visits'][0],slot))
    assert bookings[0][1]['advisor_id'] != bookings[1][1]['advisor_id']
    assert bookings[0][1]['unit_id'] == bookings[1][1]['unit_id'] == 'H-201'
    for lead, booking, slot in bookings:
        assert booking['status'] == 'confirmed'
        assert booking['advisor_name'] in lead['messages'][-1]['content'] or booking['advisor_name'] in ''.join(x['content'] for x in lead['messages'][-2:])
    assert db.delete_lead(auth, bookings[0][0]['id'])
    with db.database() as con:
        assert con.execute('SELECT COUNT(*) FROM meetings WHERE lead_id=?',(bookings[0][0]['id'],)).fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM meeting_occupancy WHERE meeting_id=?',(bookings[0][1]['meeting_id'],)).fetchone()[0] == 0


def test_fixture_dates_stay_absolute_across_reads(env):
    with db.database() as con:
        before=con.execute("SELECT starts_at FROM meetings WHERE id='demo-busy-project-a'").fetchone()[0]
    db.initialize()
    with db.database() as con:
        after=con.execute("SELECT starts_at FROM meetings WHERE id='demo-busy-project-a'").fetchone()[0]
    assert before==after


def test_fact_evidence_references_persisted_inbound_message(env,monkeypatch):
    client=TestClient(main.app)
    csrf=login(client)
    lead=add_lead(client,csrf,'Nora')
    def fake_chat(msg,history,state,*,extra_occupied=None):
        domain.update_lead(state,budget_usd=340000,purchase_timeframe='within 5 months')
        domain.track_property_interest(state,'H-101',status='interested')
        return {'reply':'The synthetic H-101 is within your budget.',
                'history':[{'role':'user','content':msg},{'role':'assistant','content':'The synthetic H-101 is within your budget.'}]}
    monkeypatch.setattr(main,'chat',fake_chat)
    response=client.post(f"/api/leads/{lead['id']}/chat",headers={'X-Demo-Csrf':csrf},
        json={'message':'I am interested in H-101; my budget is $340,000, buy in five months.'})
    assert response.status_code==200,response.text
    state=response.json()['lead']['state']
    assert state['lead']['interest_unit_id']=='H-101'
    assert state['property_interests'][0]['unit_id']=='H-101'
    assert state['property_interests'][0]['status']=='interested'
    with db.database() as con:
        messages={r[0] for r in con.execute('SELECT id FROM messages WHERE conversation_id=?',(lead['conversation_id'],))}
        facts=list(con.execute('SELECT source_message_id FROM lead_facts WHERE lead_id=?',(lead['id'],)))
        interests=list(con.execute('SELECT status,unit_id,source_message_id FROM property_interests WHERE lead_id=?',(lead['id'],)))
    assert facts and all(row[0] in messages for row in facts)
    assert interests==[('interested','H-101',interests[0][2])]
    assert interests[0][2] in messages


def test_booking_calendar_shared_between_admin_sessions(env):
    from datetime import timedelta
    from app import scheduling
    admin = TestClient(main.app)
    visitor = TestClient(main.app)
    acsrf = login(admin)
    login(visitor)
    lead = add_lead(admin,acsrf,'Private')
    auth = db.session_lookup(admin.cookies.get(main.COOKIE))
    state = lead['state']
    day = scheduling.local_now().date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    domain.begin_turn(state, 'I want H-101')
    domain.track_property_interest(state, 'H-101', 'interested')
    offer = domain.slots(state, day.isoformat(), extra_occupied=db.occupied_by_advisor())
    assert offer['ok'], offer
    domain.end_turn(state, 'Here are some times')
    db.save_turn(auth,lead['id'],0,state,[],'I want H-101','Here are some times')
    slot = offer['available_slots'][0]
    chosen_text = f'I choose {slot["start_time"]} for H-101'
    domain.begin_turn(state, chosen_text)
    assert domain.select_visit_time(state, offer['proposal_id'], slot['date'], slot['start_time'])['ok']
    domain.end_turn(state, 'Please confirm')
    db.save_turn(auth,lead['id'],1,state,[],chosen_text,'Please confirm')
    domain.begin_turn(state,'Yes, book that offered time, please')
    booked = domain.book_visit(state,'Private','Demo',lead['email'],lead['phone'],
                               date_str=slot['date'],start_time=slot['start_time'],
                               proposal_id=offer['proposal_id'],unit_id='H-101')
    assert booked['ok']
    domain.end_turn(state,'Pending database confirmation')
    result = db.save_turn(auth,lead['id'],2,state,[],'Yes, book that offered time, please','Pending database confirmation')
    booking = result['state']['booked_visits'][0]
    assert booking['advisor_name']
    params = {'start':slot['date'],'days':1}
    admin_bookings = admin.get('/api/calendar',params=params).json()['meetings']
    visitor_bookings = visitor.get('/api/calendar',params=params).json()['meetings']
    ours = next(x for x in admin_bookings if x['id'] == booking['meeting_id'])
    other = next(x for x in visitor_bookings if x['id'] == booking['meeting_id'])
    assert ours['unit_id']=='H-101' and ours['first_name']=='Private'
    assert other['unit_id']=='H-101' and other['first_name']=='Private'
    before = len(visitor.get('/api/calendar',params=params).json()['slots'])
    assert admin.delete(f"/api/admin/leads/{lead['id']}",headers={'X-Demo-Csrf':acsrf}).status_code == 200
    after = visitor.get('/api/calendar',params=params).json()
    assert len(after['slots']) >= before
    assert not any(x['id'] == booking['meeting_id'] for x in after['meetings'])


def test_insecure_demo_credentials_require_explicit_opt_in(env, monkeypatch):
    """The three 'demo' credentials work only with the explicit unsafe switch."""
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'demo')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD', 'demo')
    monkeypatch.delenv('DEMO_ALLOW_WEAK_CREDENTIALS', raising=False)
    client = TestClient(main.app)
    assert client.post('/api/auth/login', json={'credential': 'demo'}).status_code == 503
    monkeypatch.setenv('DEMO_ALLOW_WEAK_CREDENTIALS', 'true')
    assert client.post('/api/auth/login', json={'role': 'evaluator', 'credential': 'demo'}).status_code == 403
    assert client.post('/api/auth/login', json={'role': 'unknown', 'credential': 'demo'}).status_code == 403
    current = TestClient(main.app)
    response = current.post('/api/auth/login', json={'credential': 'demo'})
    assert response.status_code == 200, response.text
    assert response.json()['role'] == 'administrator'
    assert current.get('/api/me').status_code == 200
    csrf = response.json()['csrf_token']
    assert current.post('/api/auth/logout', headers={'X-Demo-Csrf': csrf}).status_code == 200
    wrong = client.post('/api/auth/login', json={'credential': 'wrong'})
    assert wrong.status_code == 401


def test_strong_credentials_still_work_with_weak_switch_disabled(env, monkeypatch):
    monkeypatch.delenv('DEMO_ALLOW_WEAK_CREDENTIALS', raising=False)
    assert main.accepted_demo_credential('a' * 32, 32)
    assert main.accepted_demo_credential('strong-admin-pass', 16)
    assert not main.accepted_demo_credential('demo', 16)
    assert not main.accepted_demo_credential('demo', 32)


def test_legacy_evaluator_session_cannot_be_reused(env):
    """Upgrade must not let old evaluator cookies access the administrator-only API."""
    import hashlib
    import uuid
    from datetime import datetime, timedelta, timezone

    token = 'previous-evaluator-session-token'
    now = datetime.now(timezone.utc)
    with db.database() as con:
        con.execute('INSERT INTO app_sessions(id,owner_id,role,token_hash,created_at,expires_at) VALUES(?,?,?,?,?,?)',
                    (str(uuid.uuid4()), str(uuid.uuid4()), 'evaluator',
                     hashlib.sha256(token.encode()).hexdigest(),
                     db.iso(now), db.iso(now+timedelta(days=1))))
    client = TestClient(main.app)
    client.cookies.set(main.COOKIE, token)
    assert client.get('/api/me').status_code == 401
    assert client.get('/api/leads').status_code == 401
    with pytest.raises(ValueError, match='Only administrator'):
        db.session_create('evaluator')
