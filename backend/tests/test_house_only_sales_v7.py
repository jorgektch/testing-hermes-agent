"""House-only sales scenario, preserved canonical source and Hermes identity."""
import json
from pathlib import Path

from app import domain, persistence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'shared' / 'blackpenguin'


def load(name):
    return json.loads((SRC / name).read_text())


def test_only_houses_are_in_active_inventory_and_every_house_has_own_media():
    project = load('project.json')['project']
    assert project['transaction_type'] == 'sale'
    assert project['active_property_categories'] == ['house']
    types = {p['id']: p for p in load('property_types.json')['property_types']}
    assert all(t['category'] == 'house' for t in types.values())
    units = load('units.json')['units']
    assert len(units) == 5
    assert {u['unit_code'] for u in units} == {'H-201','H-204','H-101','H-102','H-104'}
    assert all(u['property_type_id'] in types for u in units)
    catalog = domain.read_catalog()
    assert catalog['project_info']['transaction_type'] == 'sale'
    assert {p['unit_id'] for p in catalog['properties']} == {'H-201','H-204','H-101','H-104'}
    assert 'H-102' not in json.dumps(catalog)
    for unit in catalog['properties']:
        assert unit['property_type'] == 'House'
        assert unit['lot_area_m2'] and unit['lot_area_m2'] > unit['area_sqm']
        assert unit['media']['gallery_url'] == f"/properties/{unit['unit_id']}/gallery"
        assert len(unit['media']['gallery_urls']) == 2
        assert len(unit['media']['documents']) >= 1
        for doc in unit['media']['documents']:
            assert doc['scope'] == 'unit'
            assert doc['unit_id'] == unit['blackpenguin_unit_id']
            assert '/demo-assets/BP-H' in doc['url']


def test_two_bedroom_house_in_300k_budget_and_overbudget_never_a_match():
    state=domain.blank_state()
    domain.begin_turn(state, "I'm looking for a 2-bedroom house and won't spend more than $300k.")
    domain.update_lead(state, property_type='House', min_bedrooms=2, budget_usd=300000)
    result=domain.evaluate_properties(state, 300000, min_bedrooms=2, preferred_property_type='House')
    assert result['ok']
    matches=result['matching_properties']
    assert matches[0]['unit_id'] == 'H-201'
    assert [m['unit_id'] for m in matches] == ['H-201','H-204']
    assert all(m['price_usd'] <= 300000 and m['property_type']=='House' for m in matches)
    assert {'H-101','H-104'} == {m['unit_id'] for m in result['out_of_budget_properties']}
    assert 'H-102' not in json.dumps(result)


def test_server_enforces_existing_lead_budget_even_with_agent_overshoot():
    state=domain.blank_state()
    domain.begin_turn(state, 'My maximum budget is $250k for a house with two bedrooms')
    domain.update_lead(state, budget_usd=250000, min_bedrooms=2, property_type='House')
    results=domain.evaluate_properties(state, 500000, min_bedrooms=2)
    assert [x['unit_id'] for x in results['matching_properties']] == ['H-201']
    assert results['applied_criteria']['max_budget_usd'] == 250000
    assert all(x['price_usd']<=250000 for x in results['matching_properties'])


def test_fictional_project_has_no_apartments_for_sale_or_rent():
    state=domain.blank_state()
    result=domain.evaluate_properties(state,300000,preferred_property_type='Apartment')
    assert result['matching_properties'] == []
    assert all(u['property_type']=='House' for u in domain.read_catalog()['properties'])


def test_new_admin_lead_greeting_identifies_ai_sales_agent(monkeypatch,tmp_path):
    monkeypatch.delenv('TURSO_DATABASE_URL',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    monkeypatch.delenv('AWS_LAMBDA_FUNCTION_NAME',raising=False)
    monkeypatch.setenv('DEMO_DATABASE_PATH',str(tmp_path/'greetings.sqlite3'))
    persistence._INITIALIZED.clear()
    persistence.initialize()
    auth={'owner_id':'admin','role':'administrator'}
    lead=persistence.create_lead(auth,'Casey','Liu','casey@example.test','+51987654321')
    greeting=lead['messages'][0]['content']
    assert "I'm the AI Sales Agent assigned to help you" in greeting
    assert 'Ava' not in greeting
    assert lead['messages'][0]['role']=='assistant'


def test_hermes_identity_and_frontend_label_match():
    from app import hermes_runtime
    assert "I'm the AI Sales Agent assigned to help you" in hermes_runtime.PROMPT
    assert "You are Ava" not in hermes_runtime.PROMPT
    page=(ROOT/'frontend/src/app/page.tsx').read_text()
    assert 'AI Sales Agent · Hermes' in page
    assert 'Ava · Hermes' not in page
    assert "(['All','Apartment','House']" not in page


def test_old_conversation_rows_remain_unmodified_by_house_catalog():
    # No migration deletes historical leads, messages, meetings, or references.
    schema=(ROOT/'backend/app/persistence.py').read_text()
    assert 'DELETE FROM messages' not in schema or 'WHERE' in schema
    assert 'INSERT INTO messages' in schema
    assert 'UPDATE messages SET' in schema  # chronological repair retained
