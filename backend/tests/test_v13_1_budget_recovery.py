"""Regression from anonymized 2026-11-21 Hermes conversation export.

Only the two buyer utterances are used. No names, numbers, contact details,
conversation IDs or other personally identifying export fields are committed.
"""
from fastapi.testclient import TestClient
import pytest

import main
from app import commercial_turn, delivery_policy, domain, persistence, sales_preferences


FIRST = 'I am looking for a 2-bedroom house'
BUDGET = 'I do not want to spend more than $200k'


def state_after_first_turn():
    state=domain.blank_state()
    main._begin_turn(state,FIRST)
    out=domain.search_properties(state,{'bedrooms':2})
    assert out['ok']
    domain.end_turn(state,'H-201 is a 2-bedroom house priced at $245,000.')
    return state


def test_budget_expression_is_a_persistent_cap_not_detail_question():
    assert commercial_turn.intent(BUDGET)=='preference_update'
    assert commercial_turn.scope(BUDGET,has_primary=True)=='permanent'
    assert sales_preferences.parse_declarations(BUDGET)=={
        'budget':{'mode':'cap','maximum_usd':200000}
    }
    assert commercial_turn.extracted_criteria(BUDGET)['max_price_usd']==200000


@pytest.mark.parametrize('utterance',[
    "I do not want to spend more than $200k",
    "I don't want to spend more than $200k",
    "I dont want to spend more than $200k",
    "My maximum is $200k",
    "I can go up to $200k",
])
def test_equivalent_direct_caps(utterance):
    assert sales_preferences.parse_declarations(utterance)['budget']=={
        'mode':'cap','maximum_usd':200000}


def test_first_search_uses_primary_scope_not_accidental_temporary_scope():
    state=domain.blank_state()
    main._begin_turn(state,FIRST)
    result=domain.search_properties(state,criteria={'bedrooms':2})
    assert result['ok']
    assert result['scope']=='permanent'
    assert state['active_search']['scope']=='permanent'
    assert state['search_preferences']['bedrooms']['preferred']==2


def test_exported_two_turns_update_budget_and_recalculate_zero_matches():
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    prefs=state['search_preferences']
    assert prefs['budget']['mode']=='cap'
    assert prefs['budget']['maximum_usd']==200000
    assert prefs['bedrooms']['preferred']==2
    assert state['lead']['budget_usd']==200000
    assert state['_turn_intent']=='preference_update'
    assert state['conversation_phase']=='exploration'
    assert state['active_search']['matching_unit_ids']==[]
    assert state['active_search']['scope']=='permanent'
    assert state['_pref_updated_search_this_turn']['match_status']=='no_matches'
    assert state['_search_nearest_out_of_budget_this_turn']['unit_id']=='H-201'
    assert state['_search_nearest_out_of_budget_this_turn']['exceeds_budget_by_usd']==45000
    assert not any(x.get('status')=='rejected' for x in state['tool_observations'])
    assert len(state['search_sessions'])==2


def test_inherited_bedroom_filter_is_accepted_and_repeat_search_reuses_session():
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    count=len(state['search_sessions'])
    result=domain.search_properties(state,criteria={
        'bedrooms':2,'max_price_usd':200000})
    assert result['ok'] and result['matching_properties']==[]
    assert result['cached'] is True
    assert len(state['search_sessions'])==count
    assert state['search_preferences']['bedrooms']['preferred']==2


def test_new_unsubstantiated_bedroom_filter_is_rejected_without_mutation():
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    result=domain.search_properties(state,criteria={
        'bedrooms':4,'max_price_usd':200000})
    assert result['error_code']=='SEARCH_CRITERIA_INVALID'
    assert state['search_preferences']['bedrooms']['preferred']==2


def test_model_cannot_silently_modify_another_saved_preference():
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    result=domain.update_search_preferences(state,bedrooms_preferred=3)
    assert result['error_code']=='PREFERENCE_NOT_DECLARED'
    assert state['search_preferences']['bedrooms']['preferred']==2
    same=domain.update_search_preferences(
        state,bedrooms_mode='preferred',bedrooms_preferred=2)
    assert same['ok'] and same['changed_fields']==[]
    assert state['search_preferences']['budget']['maximum_usd']==200000


@pytest.mark.parametrize('failed_answer',[
    "I'm currently having difficulty retrieving properties that fit within your budget of $200,000 for a 2-bedroom house.",
    "Since the system requires clearer constraints for filtering, please confirm that you want to keep the preference.",
    "There are matching properties under $200k. Would you like a visit?",
    "H-201 is a 2-bedroom house listed for $245,000, which fits your budget.",
])
def test_invalid_agent_response_is_repaired_with_commercial_no_match(failed_answer):
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    answer=delivery_policy.validate_outbound(failed_answer,state)
    assert 'no' in answer.lower() or "don't currently have" in answer.lower()
    assert '$200,000' in answer
    assert 'H-201' in answer
    assert '$245,000' in answer
    assert '$45,000' in answer
    assert 'above your limit' in answer
    assert 'system' not in answer.lower()
    assert 'retrieving' not in answer.lower()
    assert 'confirm' not in answer.lower()
    assert state['search_preferences']['budget']['maximum_usd']==200000


def test_natural_valid_agent_reply_does_not_get_unnecessarily_rewritten():
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    good=("We don't have a 2-bedroom house for $200,000 or less. "
          "H-201 is an optional alternative at $245,000, $45,000 above your limit. "
          "Would you like to explore it?")
    assert delivery_policy.validate_outbound(good,state)==good


def test_next_turn_does_not_inherit_old_search_recovery_cache():
    state=state_after_first_turn()
    main._begin_turn(state,BUDGET)
    repaired=delivery_policy.validate_outbound(
        'The system requires clearer constraints for filtering.',state)
    domain.end_turn(state,repaired)
    assert '_pref_updated_search_this_turn' not in state
    assert '_search_result_cache_this_turn' not in state
    assert '_search_out_of_budget_this_turn' not in state
    main._begin_turn(state,'What is the built area of H-201?')
    assert state['_turn_intent']=='property_details'
    assert domain.unit_details(state)['area_sqm']==110
    assert state['search_preferences']['budget']['maximum_usd']==200000


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('DEMO_DATABASE_PATH',str(tmp_path/'budget-v13-1.sqlite3'))
    monkeypatch.setenv('DEMO_SIGNING_SECRET','synthetic-sample-e2e-signing-secret-long')
    monkeypatch.setenv('DEMO_ADMIN_PASSWORD','synthetic-sample-e2e-admin-password')
    monkeypatch.delenv('TURSO_DATABASE_URL',raising=False)
    monkeypatch.delenv('DEMO_PERSISTENT_TURSO_URL',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    persistence.initialize()
    return TestClient(main.app)


def test_http_fake_hermes_budget_update_is_persisted_and_replied_naturally(
        client,monkeypatch):
    def mock_agent(message,history,state,*,extra_occupied=None,on_tool_state=None):
        # Reproduce the original model's bad output. Server-verified catalog
        # search must repair it without making up an in-budget house.
        answer=("H-201 is available." if message==FIRST else
                "Since the system requires clearer constraints for filtering, "
                "please confirm your two-bedroom budget and try again.")
        return {'reply':answer,'history':history+[
            {'role':'user','content':message},{'role':'assistant','content':answer}]}
    monkeypatch.setattr(main,'chat',mock_agent)
    sign=client.post('/api/auth/login',json={
        'credential':'synthetic-sample-e2e-admin-password'})
    assert sign.status_code==200,sign.text
    headers={'X-Demo-Csrf':sign.json()['csrf_token']}
    created=client.post('/api/leads',json={
        'first_name':'Demo','last_name':'Buyer','email':'buyer@example.test',
        'phone':'+51999999999'},headers=headers)
    assert created.status_code==201,created.text
    lead_id=created.json()['id']
    def send(msg):
        response=client.post(f'/api/leads/{lead_id}/chat',
                             json={'message':msg},headers=headers)
        assert response.status_code==200,response.text
        return response.json()
    send(FIRST)
    result=send(BUDGET)
    assert '$200,000' in result['reply']
    assert 'H-201' in result['reply']
    assert '$45,000' in result['reply']
    assert 'system' not in result['reply'].lower()
    saved=client.get(f'/api/leads/{lead_id}')
    assert saved.status_code==200,saved.text
    state=saved.json()['state']
    assert state['search_preferences']['budget']['maximum_usd']==200000
    assert state['search_preferences']['bedrooms']['preferred']==2
    assert state['active_search']['matching_unit_ids']==[]
    assert state['active_search']['scope']=='permanent'
    assert state['visit_draft']['unit_id'] is None
