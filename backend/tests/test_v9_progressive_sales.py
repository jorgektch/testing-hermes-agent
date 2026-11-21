"""V9 acceptance tests for Hermes-first, progressive buyer SMS workflow."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from app import domain, persistence, delivery_policy, sms, hermes_runtime
import main

AUTH = {'role': 'administrator', 'owner_id': 'demo-admin'}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv('DEMO_DATABASE_PATH', str(tmp_path / 'v9.sqlite3'))
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('DEMO_PERSISTENT_TURSO_URL', raising=False)
    monkeypatch.delenv('VERCEL', raising=False)
    persistence.initialize()


def test_new_lead_welcome_is_in_server_owned_hermes_history(store):
    rec = persistence.create_lead(AUTH, 'Alex', 'Kim', 'a@example.test', '+51999999999')
    saved = persistence.lead_get(AUTH, rec['id'])
    assert saved['messages'][0]['role'] == 'assistant'
    assert saved['hermes_history'] == [{'role':'assistant', 'content': saved['messages'][0]['content']}]
    assert len(main.restore_initial_greeting(saved['hermes_history'], saved['messages'])) == 1
    assert 'AI Sales Agent assigned to help you' in saved['hermes_history'][0]['content']


def test_legacy_greeting_restored_once_without_rewriting_visible_messages():
    visible = [{'role':'assistant','content':"Hi Pat! I'm the AI Sales Agent assigned to help you."},
               {'role':'user', 'content':'I want a two-bedroom house.'}]
    legacy = [{'role':'user','content':'I want a two-bedroom house.'},
              {'role':'assistant','content':'What is your budget?'}]
    expected = main.restore_initial_greeting(legacy, visible)
    assert expected[0]['role'] == 'assistant'
    assert expected[0]['content'] == visible[0]['content']
    assert main.restore_initial_greeting(expected, visible) == expected
    assert legacy[0]['role'] == 'user'  # immutable input


def test_matching_is_compact_has_no_media_and_preserves_ranking():
    state = domain.blank_state()
    domain.update_lead(state, budget_usd=300000, min_bedrooms=2, property_type='House')
    res = domain.evaluate_properties(state, 300000, min_bedrooms=2)
    assert [u['unit_id'] for u in res['matching_properties']] == ['H-201','H-204']
    assert res['next_action'].startswith('Offer a brief list')
    for unit in res['matching_properties'] + res['out_of_budget_properties']:
        assert 'media' not in unit and 'gallery_urls' not in unit
        assert 'confirmed_features' not in unit and 'highlights' not in unit
        assert set(unit) <= {'unit_id','commercial_name','property_type','bedrooms','price_usd',
                             'available_from_date','status','ready_by_purchase_deadline',
                             'exceeds_budget_by_usd','mismatch_reason'}


def test_house_details_require_buyer_reference_and_do_not_select_visit():
    state = domain.blank_state()
    state['shortlisted_unit_ids'] = ['H-201', 'H-204']
    domain.begin_turn(state, 'How large is the first one?')
    result = domain.unit_details(state, 'H-201')
    assert result['ok']
    assert result['bedrooms'] == 2 and result['bathrooms'] == 2
    assert 'media' not in result and 'gallery_url' not in result
    assert state['exploring_unit_id'] == 'H-201'
    assert state['lead']['interest_unit_id'] is None
    assert 'error' in domain.unit_details(state, 'H-104')


def test_just_asking_about_house_does_not_select_it_for_visits():
    state = domain.blank_state()
    domain.begin_turn(state, 'Tell me more about H-201')
    assert domain.track_property_interest(state, 'H-201', status='discussed')['ok']
    assert 'error' in domain.track_property_interest(state, 'H-201', status='interested')
    assert state['exploring_unit_id'] == 'H-201'
    assert state['lead']['interest_unit_id'] is None
    domain.end_turn(state, 'H-201 has two bedrooms.')
    domain.begin_turn(state, 'I want to visit H-201')
    assert domain.track_property_interest(state,'H-201',status='interested')['ok']
    assert state['lead']['interest_unit_id'] == 'H-201'


def test_matching_reply_stays_one_gsm_sms():
    matches = [{'unit_id':'H-201','bedrooms':2,'price_usd':245000},
               {'unit_id':'H-204','bedrooms':3,'price_usd':295000}]
    reply = delivery_policy._matching_reply(matches)
    assert 'H-201' in reply and 'H-204' in reply
    assert 'https://' not in reply
    assert sms.details(reply)['encoding'] == 'GSM-7'
    assert sms.details(reply)['segments'] == 1


def test_guardrail_replaces_verbose_search_with_grounded_shortlist():
    state = domain.blank_state()
    state['_matching_options_this_turn'] = [
        {'unit_id':'H-201','bedrooms':2,'price_usd':245000},
        {'unit_id':'H-204','bedrooms':3,'price_usd':295000}]
    bloated = ('These houses have multiple amenities and exciting details. ' * 9 +
               'https://demo-assets/H-201/gallery')
    reply = delivery_policy.validate_outbound(bloated, state)
    assert 'H-201' in reply and 'H-204' in reply
    assert sms.details(reply)['segments'] == 1
    assert 'demo-assets' not in reply


def test_guardrail_removes_redundant_self_introduction():
    text = "I'm the AI Sales Agent assigned to help you. Could you tell me your budget?"
    assert delivery_policy.validate_outbound(text, {}) == 'Could you tell me your budget?'


def test_media_urls_must_be_explicitly_approved_by_tool():
    state = domain.blank_state()
    approved = domain.unit_media('H-201')['gallery_url']
    assert approved.startswith('https://')
    state['_approved_media_urls_this_turn'] = [approved]
    assert delivery_policy.validate_outbound('Photos: ' + approved, state).endswith(approved)
    with pytest.raises(ValueError, match='not authorized'):
        delivery_policy.validate_outbound('Photos: https://demo-assets/H-201/gallery', state)
    with pytest.raises(ValueError, match='not authorized'):
        delivery_policy.validate_outbound('Photos: ' + approved, {})
    with pytest.raises(ValueError, match='not authorized'):
        delivery_policy.validate_outbound('Gallery: /properties/H-201/gallery', state)


def test_media_tool_refuses_unsolicited_media_and_releases_one_requested_link(monkeypatch):
    handlers = {}
    registry = SimpleNamespace(register=lambda **kw: handlers.update({kw['name']:kw['handler']}))

    class Agent:
        def __init__(self, **kw):
            self.valid_tool_names = list(handlers)
            assert kw['enabled_toolsets'] == [hermes_runtime.TOOLSET]
        def run_conversation(self, message, conversation_history):
            requested = json.loads(handlers['get_unit_media']({'unit_id':'H-201'}))
            if message == 'Tell me about H-201':
                assert 'error' in requested
                response = 'H-201 is a two-bedroom house. Would you like photos?'
            elif message == 'Photos of H-201 please':
                assert requested['ok']
                assert 'documents' not in requested
                assert requested['gallery_url'].endswith('/properties/H-201/gallery')
                response = requested['gallery_url']
            else:
                raise AssertionError(message)
            return {'final_response':response,
                    'messages':[{'role':'assistant','content':response}]}

    monkeypatch.setitem(sys.modules, 'run_agent', ModuleType('run_agent'))
    sys.modules['run_agent'].AIAgent = Agent
    monkeypatch.setitem(sys.modules, 'tools', ModuleType('tools'))
    sys.modules['tools'].__path__ = []
    monkeypatch.setitem(sys.modules, 'tools.registry', ModuleType('tools.registry'))
    sys.modules['tools.registry'].registry = registry
    monkeypatch.setitem(sys.modules, 'model_tools', ModuleType('model_tools'))
    sys.modules['model_tools'].get_tool_definitions = lambda *a, **kw: []
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-nonreal-api-key')
    state = domain.blank_state()
    domain.begin_turn(state, 'Tell me about H-201')
    text = hermes_runtime.chat('Tell me about H-201', [], state)['reply']
    assert 'photos' in text.lower()
    assert not state.get('_approved_media_urls_this_turn')
    domain.end_turn(state,text)
    domain.begin_turn(state, 'Photos of H-201 please')
    text = hermes_runtime.chat('Photos of H-201 please', [], state)['reply']
    assert text in state['_approved_media_urls_this_turn']
    assert delivery_policy.validate_outbound(text,state) == text


def test_single_media_offer_accepts_yes_but_ambiguous_offer_does_not():
    state = domain.blank_state()
    domain.begin_turn(state, 'Tell me about H-201')
    assert domain.track_property_interest(state, 'H-201', 'discussed')['ok']
    domain.end_turn(state, 'Would you like the photo gallery for H-201?')
    assert state['pending_media_offer'] == {'unit_id':'H-201','kind':'gallery'}
    domain.begin_turn(state, 'Yes please')
    assert state['pending_media_offer']['kind'] == 'gallery'
    domain.end_turn(state, 'The gallery is ready.')
    assert state['pending_media_offer'] is None
    domain.begin_turn(state, 'Yes')
    domain.end_turn(state, 'Would you like photos or to schedule a visit?')
    assert state['pending_media_offer'] is None


def test_numeric_choice_never_selects_unlisted_house():
    state = domain.blank_state()
    state['shortlisted_unit_ids'] = ['H-201','H-204']
    domain.begin_turn(state, 'More about the second one')
    assert domain.unit_details(state, 'H-204')['ok']
    assert domain.unit_details(state, 'H-101').get('error')
    assert state['lead']['interest_unit_id'] is None


def test_shortlist_fallback_cannot_accidentally_recommend_over_budget_unit():
    state=domain.blank_state()
    state['_matching_options_this_turn']=[{'unit_id':'H-201','bedrooms':2,'price_usd':245000}]
    model_reply='H-104 at $395,000 is within your $300k budget.'
    safe=delivery_policy.validate_outbound(model_reply, state)
    assert 'H-201' in safe and 'H-104' not in safe
    assert sms.details(safe)['segments'] == 1
