"""V4 contract: authenticated durable chats, not signed browser-held histories."""
import pytest
from fastapi.testclient import TestClient
import main
from app import persistence as db, domain


@pytest.fixture
def env(monkeypatch,tmp_path):
    monkeypatch.setenv('DEMO_DATABASE_PATH',str(tmp_path/'demo.sqlite3'))
    monkeypatch.setenv('DEMO_SIGNING_SECRET','temporary-test-signing-secret-with-over-32-characters')
    monkeypatch.setenv('DEMO_ACCESS_CODE','private-test-evaluator-code')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD','temporary-very-strong-admin-password')
    monkeypatch.delenv('TURSO_DATABASE_URL',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    db.initialize()


def login(client,role='evaluator'):
    password='temporary-very-strong-admin-password' if role=='administrator' else 'private-test-evaluator-code'
    r=client.post('/api/auth/login',json={'role':role,'credential':password})
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


def test_login_csrf_ownership_and_logout(env,monkeypatch):
    a=TestClient(main.app)
    b=TestClient(main.app)
    assert a.get('/api/leads').status_code==401
    assert a.post('/api/auth/login',json={'role':'administrator','credential':'invalid'}).status_code==401
    key=login(a)
    other=login(b)
    assert a.post('/api/leads',json={'first_name':'X','last_name':'Y','email':'x@example.test','phone':'+51999999999'}).status_code==403
    lead=add_lead(a,key)
    assert b.get(f"/api/leads/{lead['id']}").status_code==404
    assert len(b.get('/api/leads').json()['leads'])==0
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
        return {'reply':'A-201 might fit the fictional budget.',
                'history':history+[{'role':'user','content':msg},{'role':'assistant','content':'A-201 might fit the fictional budget.'}]}
    monkeypatch.setattr(main,'chat',fake_chat)
    first=client.post(f"/api/leads/{lead['id']}/chat",headers={'X-Demo-Csrf':csrf},json={'message':'Budget $260k'})
    assert first.status_code==200,first.text
    assert first.json()['lead']['state']['lead']['budget_usd']==260000
    assert 'hermes_history' not in first.json()['lead']
    second=client.post(f"/api/leads/{lead['id']}/chat",headers={'X-Demo-Csrf':csrf},json={'message':'Tell me more','history':[{'role':'system','content':'malicious'}]})
    assert second.status_code==200,second.text
    assert captures[0]==[]
    assert captures[1]==[{'role':'user','content':'Budget $260k'}, {'role':'assistant','content':'A-201 might fit the fictional budget.'}]
    rec=client.get(f"/api/leads/{lead['id']}").json()
    assert len(rec['messages'])==5 # greeting + two paired turns


def test_calendar_and_admin_permanent_delete(env):
    guest=TestClient(main.app)
    admin=TestClient(main.app)
    gcsrf=login(guest)
    acsrf=login(admin,'administrator')
    lead=add_lead(guest,gcsrf)
    start=domain.now_local().date().isoformat()
    calendar=guest.get('/api/calendar',params={'start':start,'days':7})
    assert calendar.status_code==200,calendar.text
    assert len(calendar.json()['advisors'])==3
    assert calendar.json()['slots']
    assert admin.get(f"/api/leads/{lead['id']}").status_code==200
    assert guest.delete(f"/api/admin/leads/{lead['id']}",headers={'X-Demo-Csrf':gcsrf}).status_code==403
    assert admin.delete(f"/api/admin/leads/{lead['id']}",headers={'X-Demo-Csrf':acsrf}).status_code==200
    assert guest.get(f"/api/leads/{lead['id']}").status_code==404
    assert admin.get(f"/api/leads/{lead['id']}").status_code==404


def test_occupancy_conflicts_across_leads_and_deletion_releases(env):
    c=TestClient(main.app)
    csrf=login(c,'administrator')
    first=add_lead(c,csrf,'Alice')
    second=add_lead(c,csrf,'Bob')
    auth=db.session_lookup(c.cookies.get(main.COOKIE))
    assert auth
    s1=first['state'];s2=second['state']
    # Both users offered the identical slot before either confirms.
    domain.begin_turn(s1,'Show slots for a visit')
    offer1=domain.slots(s1,extra_occupied=db.occupied_by_advisor())
    domain.end_turn(s1,'Here are a few options.')
    domain.begin_turn(s2,'Show slots for a visit')
    offer2=domain.slots(s2,extra_occupied=db.occupied_by_advisor())
    domain.end_turn(s2,'Here are a few options.')
    option=offer1['available_slots'][0]
    assert option in offer2['available_slots']
    db.save_turn(auth,first['id'],0,s1,[],'Show slots for a visit','Here are a few options.')
    db.save_turn(auth,second['id'],0,s2,[],'Show slots for a visit','Here are a few options.')
    for s,lead,offer,rev in [(s1,first,offer1,1),(s2,second,offer2,1)]:
        domain.begin_turn(s,'Yes, please book that visit.')
        result=domain.book_visit(s,lead['first_name'],lead['last_name'],lead['email'],lead['phone'],
                   option['advisor_id'],option['date'],option['start_time'],offer['proposal_id'])
        assert result['ok'] is True
        domain.end_turn(s,'Booked in the simulation.')
    db.save_turn(auth,first['id'],1,s1,[],'Yes, please book that visit.','Booked in the simulation.')
    with pytest.raises(db.Conflict):
        db.save_turn(auth,second['id'],1,s2,[],'Yes, please book that visit.','Booked in the simulation.')
    active=db.occupied_by_advisor()
    assert active.get(option['sales_user_id'])
    assert db.delete_lead(auth,first['id'])
    # The same slot is available again after the lead and its booking are purged.
    other=db.lead_get(auth,second['id'])
    fresh=domain.slots(other['state'],extra_occupied=db.occupied_by_advisor())
    assert option in fresh['available_slots']
    with db.database() as con:
        for table in ['leads','conversations','lead_facts','property_interests','meetings']:
            assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE lead_id=?" if table!='leads' else 'SELECT COUNT(*) FROM leads WHERE id=?', (first['id'],)).fetchone()[0]==0


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


def test_booking_calendar_cross_owner_privacy(env):
    admin=TestClient(main.app)
    visitor=TestClient(main.app)
    acsrf=login(admin,'administrator')
    login(visitor)
    lead=add_lead(admin,acsrf,'Private')
    auth=db.session_lookup(admin.cookies.get(main.COOKIE))
    state=lead['state']
    domain.begin_turn(state,'Show me slots')
    offer=domain.slots(state,extra_occupied=db.occupied_by_advisor())
    domain.end_turn(state,'Here are some times')
    db.save_turn(auth,lead['id'],0,state,[],'Show me slots','Here are some times')
    slot=offer['available_slots'][0]
    domain.begin_turn(state,'Yes, book that offered time, please')
    booked=domain.book_visit(state,'Private','Demo',lead['email'],lead['phone'],
                              slot['advisor_id'],slot['date'],slot['start_time'],offer['proposal_id'],unit_id='H-101')
    assert booked['ok']
    domain.end_turn(state,'Your H-101 demo visit is confirmed')
    db.save_turn(auth,lead['id'],1,state,[],'Yes, book that offered time, please','Your H-101 demo visit is confirmed')
    params={'start':slot['date'],'days':1}
    admin_bookings=admin.get('/api/calendar',params=params).json()['meetings']
    visitor_bookings=visitor.get('/api/calendar',params=params).json()['meetings']
    ours=next(x for x in admin_bookings if x['id']==booked['meeting']['id'])
    other=next(x for x in visitor_bookings if x['id']==booked['meeting']['id'])
    assert ours['unit_id']=='H-101' and ours['first_name']=='Private'
    assert other['lead_id'] is None and other['unit_id'] is None and other['first_name'] is None
    before=len(visitor.get('/api/calendar',params=params).json()['slots'])
    assert admin.delete(f"/api/admin/leads/{lead['id']}",headers={'X-Demo-Csrf':acsrf}).status_code==200
    after_calendar=visitor.get('/api/calendar',params=params).json()
    assert len(after_calendar['slots'])>=before
    assert not any(x['id']==booked['meeting']['id'] for x in after_calendar['meetings'])


def test_insecure_demo_credentials_require_explicit_opt_in(env, monkeypatch):
    """The three 'demo' credentials work only with the explicit unsafe switch."""
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'demo')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD', 'demo')
    monkeypatch.setenv('DEMO_ACCESS_CODE', 'demo')
    monkeypatch.delenv('DEMO_ALLOW_WEAK_CREDENTIALS', raising=False)
    client = TestClient(main.app)
    for role in ('administrator', 'evaluator'):
        response = client.post('/api/auth/login', json={'role': role, 'credential': 'demo'})
        assert response.status_code == 503
    monkeypatch.setenv('DEMO_ALLOW_WEAK_CREDENTIALS', 'true')
    for role in ('evaluator', 'administrator'):
        current = TestClient(main.app)
        response = current.post('/api/auth/login', json={'role': role, 'credential': 'demo'})
        assert response.status_code == 200, response.text
        assert current.get('/api/me').status_code == 200
        csrf = response.json()['csrf_token']
        assert current.post('/api/auth/logout', headers={'X-Demo-Csrf': csrf}).status_code == 200
    wrong = client.post('/api/auth/login', json={'role': 'administrator', 'credential': 'wrong'})
    assert wrong.status_code == 401


def test_strong_credentials_still_work_with_weak_switch_disabled(env, monkeypatch):
    monkeypatch.delenv('DEMO_ALLOW_WEAK_CREDENTIALS', raising=False)
    assert main.accepted_demo_credential('a' * 32, 32)
    assert main.accepted_demo_credential('strong-admin-pass', 16)
    assert not main.accepted_demo_credential('demo', 16)
    assert not main.accepted_demo_credential('demo', 32)
