"""Upload-ready integration regressions; synthetic data and no live Hermes API."""
from __future__ import annotations

import pytest
import main
from app import domain, property_options, sms, delivery_policy


def make_menu():
    state = domain.blank_state()
    main._begin_turn(state, 'I am looking for a 2-bedroom house')
    assert domain.search_properties(state, {'bedrooms': 2})['ok']
    answer = delivery_policy.validate_outbound('H-201 is available.', state)
    domain.end_turn(state, answer)
    main._begin_turn(state, 'Do you have any with 3 bedrooms?')
    response = domain.search_properties(state, {'bedrooms': 3})
    assert response['ok']
    assert [u['unit_id'] for u in response['matching_properties']] == ['H-204', 'H-101']
    # Exact defect reported: the model flattens two choices into one line.
    broken = ('I found two houses: 1. Three-Bedroom Courtyard House $295k '
              '2. Three-Bedroom Family House $320k. Which do you like?')
    answer = delivery_policy.validate_outbound(broken, state)
    assert '\n1. H-204 (3 beds, $295k)' in answer
    assert '\n2. H-101 (3 beds, $320k)' in answer
    domain.end_turn(state, answer)
    return state, answer


def test_order_is_persisted_as_delivered_not_prior_shortlist():
    state, answer = make_menu()
    snap = state['last_presented_property_options']
    assert [v['unit_id'] for v in snap['options']] == ['H-204', 'H-101']
    assert snap['source_reply_message_id'] == state['bp_episode']['sales_messages'][-1]['id']
    assert snap['search_revision'] == state['active_search']['revision']
    assert state['bp_episode']['sales_dialogue_states']['state_json'][
        'last_presented_property_options'] == snap
    assert sms.outgoing_segments(answer) == [answer]
    assert '\n1. H-204' in '\n'.join(sms.outgoing_segments(answer))


@pytest.mark.parametrize('text', ['The second option', 'Option 2', '2',
                                  'Option 2 its ok for me', 'I choose the $320k house'])
def test_choice_2_opens_h101_details_only(text):
    state, _ = make_menu()
    before_sessions = len(state['search_sessions'])
    before_pref = state['search_preferences'].copy()
    assert before_pref['bedrooms']['preferred'] == 2
    before_visits = len(state['booked_visits'])
    main._begin_turn(state, text)
    assert state['_turn_intent'] == 'property_selection'
    assert state['_turn_property_resolution']['unit_id'] == 'H-101'
    assert state['conversation_focus']['unit_id'] == 'H-101'
    assert state['conversation_phase'] == 'property_details'
    assert len(state['search_sessions']) == before_sessions
    assert state['search_preferences'] == before_pref
    assert len(state['booked_visits']) == before_visits
    assert domain.search_properties(state)['error_code'] == 'PROPERTY_SELECTION_NOT_SEARCH'
    assert domain.slots(state, date_str='next week')['error_code'] == 'VISIT_NOT_REQUESTED'
    assert domain.unit_details(state)['unit_id'] == 'H-101'
    incorrect = 'I found 1 matching house(s): H-201 (2 beds, $245k). Which would you like?'
    answer = delivery_policy.validate_outbound(incorrect, state)
    assert 'H-101' in answer and 'H-201' not in answer
    assert '$320,000' in answer
    domain.end_turn(state, answer)
    assert len(state['search_sessions']) == before_sessions
    assert state['last_presented_property_options']['options'][1]['unit_id'] == 'H-101'


def test_unverified_second_option_asks_instead_of_guessing():
    state = domain.blank_state()
    state['shortlisted_unit_ids'] = ['H-201', 'H-204']
    state['last_presented_units'] = ['H-201', 'H-204']
    main._begin_turn(state, 'The second option')
    assert state['_turn_property_resolution']['ambiguous']
    assert state['conversation_focus']['unit_id'] is None
    answer = delivery_policy.validate_outbound('I will book H-204 now.', state)
    assert 'Which house' in answer
    assert not state['booked_visits']


def test_fresh_search_replaces_old_menu():
    state, _ = make_menu()
    main._begin_turn(state, 'Now show me 2-bedroom homes')
    assert state['_turn_intent'] == 'property_search'
    assert domain.search_properties(state)['ok']
    answer = delivery_policy.validate_outbound('H-201 is available.', state)
    domain.end_turn(state, answer)
    assert [o['unit_id'] for o in state['last_presented_property_options']['options']] == ['H-201']
    main._begin_turn(state, 'Option 2')
    assert state['_turn_property_resolution']['ambiguous']
    assert state['conversation_focus']['unit_id'] != 'H-101'


def test_previous_appointment_draft_stays_but_consent_is_invalidated():
    state, _ = make_menu()
    state['visit_draft'].update(unit_id='H-204', date='2026-11-26',
                                start_time='09:00', status='awaiting_confirmation')
    state['confirmation_request'] = {'unit_id': 'H-204', 'date': '2026-11-26',
                                     'start_time': '09:00'}
    main._begin_turn(state, 'Option 2 its ok for me')
    assert state['visit_draft']['unit_id'] == 'H-204'
    assert state['visit_draft']['start_time'] == '09:00'
    assert state['visit_planning_paused'] is True
    assert state['confirmation_request'] is None
    assert state['conversation_focus']['unit_id'] == 'H-101'


def test_multiple_options_are_plain_text_not_inline_markdown():
    matches = [
        {'unit_id': 'H-204', 'bedrooms': 3, 'price_usd': 295000},
        {'unit_id': 'H-101', 'bedrooms': 3, 'price_usd': 320000},
    ]
    answer = property_options.render_matches(matches)
    assert answer == ('I found 2 3-bedroom houses:\n\n'
                      '1. H-204 (3 beds, $295k)\n'
                      '2. H-101 (3 beds, $320k)\n\n'
                      'Which would you like to explore? '
                      'I can also look for homes with different features.')
    assert sms.outgoing_segments(answer) == [answer]
    assert not property_options.needs_listing_repair(answer, matches)


def test_single_choice_has_no_ordinal_number():
    answer = property_options.render_matches([
        {'unit_id': 'H-201', 'bedrooms': 2, 'price_usd': 245000}])
    assert '\nH-201 (2 beds, $245k)\n' in answer
    assert '\n1.' not in answer


def test_selected_unit_wrong_price_or_bedrooms_is_repaired():
    catalog_unit = {
        'unit_id': 'H-101', 'commercial_name': 'Three-Bedroom Family House',
        'price_usd': 320000, 'bedrooms': 3, 'bathrooms': 2,
        'area_sqm': 150,
    }
    assert property_options.needs_selection_repair(
        'H-101 is a 2-bedroom house for $320k.', catalog_unit)
    assert property_options.needs_selection_repair(
        'H-101 has 3 bedrooms and costs $245k.', catalog_unit)
    assert not property_options.needs_selection_repair(
        'H-101 has 3 bedrooms and costs $320k.', catalog_unit)
