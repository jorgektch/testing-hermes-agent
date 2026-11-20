from fastapi.testclient import TestClient
import main

client = TestClient(main.app)


def test_public_catalog_and_health():
    catalog = client.get('/api/catalog')
    assert catalog.status_code == 200
    assert len(catalog.json()['properties']) == 4
    assert client.get('/api/health').status_code == 200


def test_access_code_protects_session(monkeypatch):
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'local-test-signing-secret')
    monkeypatch.setenv('DEMO_ACCESS_CODE', 'private-demo')
    assert client.post('/api/session').status_code == 401
    success = client.post('/api/session', headers={'X-Demo-Access-Code':'private-demo'})
    assert success.status_code == 200
    assert success.json()['state']['turn_count'] == 0


def test_one_turn_returns_signed_updated_state(monkeypatch):
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'local-test-signing-secret')
    monkeypatch.setenv('DEMO_ACCESS_CODE', '')
    start = client.post('/api/session').json()
    def fake_hermes_chat(message, history, state):
        from app import domain
        domain.update_lead(state, budget_usd=175000, purchase_timeframe='within 2 months')
        domain.evaluate_properties(state, 175000, 'within 2 months', 2)
        return {'reply':'A fictional $149,000 unit could be a match.',
                'history':[{'role':'user','content':message},
                           {'role':'assistant','content':'A fictional $149,000 unit could be a match.'}]}
    monkeypatch.setattr(main, 'chat', fake_hermes_chat)
    r = client.post('/api/chat', json={'token': start['token'], 'history': [],
                                       'message': 'Budget is 175k, buy within two months.'})
    assert r.status_code == 200
    data = r.json()
    assert data['state']['lead']['budget_usd'] == 175000
    assert data['state']['turn_count'] == 1
    assert data['state']['shortlisted_unit_ids'] == ['EV-A101']
    assert data['token'] != start['token']
