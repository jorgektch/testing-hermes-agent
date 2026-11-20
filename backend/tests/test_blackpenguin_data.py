"""Regression coverage for BP source-aligned synthetic JSON and public projection."""
import copy
import json
from pathlib import Path

from app import domain

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'shared'/'blackpenguin'


def read(name):
    return json.loads((DATA/name).read_text(encoding='utf-8'))


def test_source_reference_contracts_not_mistaken_for_production():
    project=read('project.json')
    example=read('lead_example.json')
    template=read('lead.json')
    assert project['example_only'] is True
    assert project['schema_version']=='hermes-demo/project/3.0'
    assert project['demo_derivation']['not_for_real_sales']
    assert example['_meta']['source_file']=='BlackPenguin_Lead_y_Dialogo_ejemplo.json'
    assert template['_meta']['not_a_database_row'] is True
    assert template['leads']['full_name'] is None
    assert template['leads']['phone'] is None
    assert template['leads']['lead_profile_data']['sales_profile']['current']=={}


def test_relationships_and_reserved_unit_exclusion():
    bp=read('project.json')
    types={t['id'] for t in read('property_types.json')['property_types']}
    bp['units']=read('units.json')['units']
    bp['property_types']=read('property_types.json')['property_types']
    unit_ids={u['id'] for u in bp['units']}
    assert len(types)==4 and len(unit_ids)==5
    assert len([u for u in bp['units'] if u['status']=='available'])==4
    assert any(u['unit_code']=='H-102' and u['status']=='reserved' for u in bp['units'])
    for typ in bp['property_types']:
        assert typ['project_id']==bp['project']['id']
    for u in bp['units']:
        assert u['property_type_id'] in types
        assert u['project_id']==bp['project']['id']
    public_units={x['blackpenguin_unit_id'] for x in domain.read_catalog()['properties']}
    assert all(next(u['status'] for u in bp['units'] if u['id']==pid)=='available' for pid in public_units)


def test_internal_sources_never_leak_to_agent_catalog():
    bp=read('project.json')
    internal_paths={s.get('storage_path') or s.get('url') for s in read('assets.json')['sources'] if s.get('sharing_intent')=='internal'}
    public_text=json.dumps(domain.read_catalog())
    assert not any(path and path in public_text for path in internal_paths)
    assert all(p['inventory_is_live'] is False for p in domain.read_catalog()['properties'])


def test_references_to_media_resolve_to_local_files():
    catalog=domain.read_catalog()
    paths={catalog['project_info']['general_brochure_pdf']}
    for unit in catalog['properties']:
        paths.update(unit['media']['gallery_urls'])
        if unit['media']['floorplan_brochure_pdf']:
            paths.add(unit['media']['floorplan_brochure_pdf'])
    assert len(paths)==10
    assert all((ROOT/'frontend'/'public'/p.lstrip('/')).exists() for p in paths)


def test_declared_fact_revisions_and_dialogue_selection():
    state=domain.blank_state()
    domain.begin_turn(state,'My budget is $300,000 and I like A-201.')
    domain.update_lead(state,budget_usd=300000,interest_unit_id='A-201')
    domain.end_turn(state,'That apartment is a demo choice.')
    domain.begin_turn(state,'I can raise my budget to $350,000.')
    domain.update_lead(state,budget_usd=350000)
    domain.end_turn(state,'Understood.')
    bp=state['bp_episode']
    profile=bp['leads']['lead_profile_data']['sales_profile']
    assert profile['current']['budget']['value']=='USD 350000'
    assert len(profile['history'])==1
    assert profile['current']['budget']['supersedes']==profile['history'][0]['id']
    assert bp['sales_dialogue_states']['state_json']['selected_unit_id']==domain.read_catalog()['properties'][0]['blackpenguin_unit_id']
    assert len(bp['sales_messages'])==4
    assert bp['leads']['consent_status']=='unknown'


def test_lead_example_references_valid_project_entities():
    project=read('project.json')
    example=read('lead_example.json')
    assert example['ids']['project_id']==project['project']['id']
    assert example['ids']['property_type_id'] in {x['id'] for x in read('property_types.json')['property_types']}
    assert example['ids']['unit_id'] in {x['id'] for x in read('units.json')['units']}
    assert example['sales_dialogue_states']['state_json']['selected_property_type_id']==example['ids']['property_type_id']


def test_all_sixteen_supported_profile_keys_can_be_stored_as_declarations():
    state=domain.blank_state()
    domain.begin_turn(state, 'My maximum budget is USD 350000. I need a quiet area, an open kitchen, and want to visit. Please explain financing in person.')
    values={
        'budget':'USD 350000','budget_minimum':300000,'budget_maximum':350000,
        'budget_currency':'USD','property_interest':'H-101','bedrooms':3,
        'bathrooms':'2.5','location_preference':'quiet area',
        'purchase_timeline':'within 3 months','financing':'ask the human advisor onsite',
        'buyer_type':'end_user','motivation':'family home',
        'decision_structure':'joint decision','move_in_timing':'after December',
        'visit_readiness':'ready to visit','needs':'open kitchen',
    }
    assert set(values)==domain.BP_COMMERCIAL_FIELDS
    result=domain.update_lead(state,**values)
    assert result['ok']
    current=state['bp_episode']['leads']['lead_profile_data']['sales_profile']['current']
    assert set(current)==domain.BP_COMMERCIAL_FIELDS
    assert current['financing']['status']=='declared'
    assert current['financing']['source_ref']['message_id']==state['_active_source_message_id']
    assert state['lead']['budget_usd']==350000
    assert state['lead']['min_bedrooms']==3
    assert state['lead']['purchase_timeframe']=='within 3 months'
    assert state['lead']['interest_unit_id']=='H-101'
    assert state['bp_episode']['leads']['funnel_stage']=='qualified'
