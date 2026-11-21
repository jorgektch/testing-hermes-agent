"""End-to-end HTTP -> Hermes adapter -> delivery guard -> Turso-like persistence.

Uses fake model output to force the precise v12 regression: an old H-201 price
response to an H-204 alternatives request during an unfinished visit.
"""
from fastapi.testclient import TestClient
import pytest
import main
from app import domain, persistence


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('DEMO_DATABASE_PATH',str(tmp_path/'v13-flow.sqlite3'))
    monkeypatch.setenv('DEMO_SIGNING_SECRET','v13-fictional-e2e-signing-secret-32-characters')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD','v13-fictional-e2e-admin-password')
    monkeypatch.delenv('TURSO_DATABASE_URL',raising=False)
    monkeypatch.delenv('DEMO_PERSISTENT_TURSO_URL',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    persistence.initialize()
    return TestClient(main.app)


def test_http_flow_resumes_search_from_pending_visit_and_persists_correct_result(
        client,monkeypatch):
    def fake_chat(msg,history,state,*,extra_occupied=None,on_tool_state=None):
        if '2-bedroom' in msg:
            answer='What is your budget?'
        elif 'more than $300k' in msg:
            answer='H-201 has 2 bedrooms and costs $245k. Would you like details?'
        elif 'want to visit H-201' in msg:
            answer='For H-201, which date would you like to visit?'
        elif 'another option with 3 bedrooms' in msg:
            # Deliberately reproduce the observed Hermes/model failure.
            answer='Two-Bedroom Garden House (H-201) is listed for USD 245,000 (synthetic).'
        else:
            answer='H-204 is a three-bedroom option. What else can I clarify?'
        return {'reply':answer,'history':history+[
            {'role':'user','content':msg},{'role':'assistant','content':answer}]}
    monkeypatch.setattr(main,'chat',fake_chat)
    sign=client.post('/api/auth/login',json={
        'credential':'v13-fictional-e2e-admin-password'})
    assert sign.status_code==200,sign.text
    headers={'X-Demo-Csrf':sign.json()['csrf_token']}
    created=client.post('/api/leads',json={
        'first_name':'Synthetic','last_name':'Buyer','email':'buyer@example.test',
        'phone':'+51999999999'},headers=headers)
    assert created.status_code==201,created.text
    lid=created.json()['id']
    def send(message):
        r=client.post(f'/api/leads/{lid}/chat',
                      json={'message':message},headers=headers)
        assert r.status_code==200,r.text
        return r.json()
    send('I am looking for a 2-bedroom house')
    send("I don't want to spend more than $300k")
    planned=send('I want to visit H-201')
    assert planned['lead']['state']['visit_draft']['unit_id']=='H-201'
    response=send('First I want to check if there is another option with 3 bedrooms under my budget')
    assert 'H-204' in response['reply']
    assert 'H-201' not in response['reply']
    state=response['lead']['state']
    assert state['search_preferences']['bedrooms']['preferred']==2
    assert state['search_preferences']['budget']['maximum_usd']==300000
    assert state['active_search']['scope']=='temporary'
    assert state['active_search']['matching_unit_ids']==['H-204']
    assert state['visit_planning_paused']
    assert state['visit_draft']['unit_id']=='H-201'
    assert state['conversation_phase']=='exploration'
    continued=send('Tell me about H-204')
    follow=continued['lead']['state']
    assert follow['conversation_focus']['unit_id']=='H-204'
    assert follow['visit_draft']['unit_id']=='H-201'
    assert follow['search_preferences']['bedrooms']['preferred']==2
    saved=client.get(f'/api/leads/{lid}')
    assert saved.status_code==200
    assert saved.json()['state']['active_search']['matching_unit_ids']==['H-204']
