"""Conversation regressions: buyer-visible menu -> ordinal selection -> Details.

Exercises the deterministic domain + SMS boundary with synthetic inventory,
no OpenRouter credentials, no real phone calls or booking transactions.
"""
from __future__ import annotations

import pytest
import main
from app import commercial_turn, conversation_context, delivery_policy, domain, property_options, sms


def fresh_menu():
    state=domain.blank_state()
    main._begin_turn(state,'I am looking for a 2-bedroom house')
    assert domain.search_properties(state,{'bedrooms':2})['ok']
    first=delivery_policy.validate_outbound('I have H-201 for you.',state)
    domain.end_turn(state,first)
    main._begin_turn(state,'Do you have any with 3 bedrooms?')
    results=domain.search_properties(state,{'bedrooms':3})
    assert results['ok']
    assert [p['unit_id'] for p in results['matching_properties']]==['H-204','H-101']
    # The regression: Hermes originally gave a one-line numbered list without IDs.
    unformatted=("I found two 3-bedroom houses for you: 1. Three-Bedroom Courtyard House; "
                 "Price: $295,000 2. Three-Bedroom Family House; Price: $320,000 "
                 "Would you like to know more about either?")
    reply=delivery_policy.validate_outbound(unformatted,state)
    assert '\n1. H-204 -' in reply and '\n2. H-101 -' in reply
    domain.end_turn(state,reply)
    return state,reply


def test_buyer_option_two_resolves_h101_without_search_or_visit():
    state,_=fresh_menu()
    snapshot=state['last_presented_property_options']
    assert [x['unit_id'] for x in snapshot['options']]==['H-204','H-101']
    assert snapshot['search_revision']==state['active_search']['revision']
    assert snapshot['source_reply_message_id']
    before=len(state['search_sessions'])
    domain.begin_turn(state,'Option 2 its ok for me')
    assert state['_turn_intent']=='property_selection'
    assert state['_turn_property_resolution']['unit_id']=='H-101'
    assert state['conversation_phase']=='property_details'
    assert state['conversation_focus']['unit_id']=='H-101'
    assert state['visit_draft']['unit_id'] is None
    assert len(state['search_sessions'])==before
    assert state['search_preferences']['bedrooms']['preferred']==2
    assert any(p['unit_id']=='H-101' and p['status']=='considering'
               for p in state['property_interests'])
    assert domain.unit_details(state)['unit_id']=='H-101'
    bad=domain.search_properties(state)
    assert bad['error_code']=='PROPERTY_SELECTION_NOT_SEARCH'
    blocked=domain.slots(state,date_str='next week')
    assert blocked['error_code']=='VISIT_NOT_REQUESTED'
    answer=delivery_policy.validate_outbound(
        'I found 1 matching house(s): H-201 (2 beds, $245k). Which would you like to explore?',state)
    assert 'H-101' in answer and 'H-201' not in answer
    assert '$320,000' in answer
    domain.end_turn(state,answer)
    assert state['last_presented_property_options']==snapshot  # Details do not replace menu history.
    assert state['conversation_phase']=='property_details'


def test_single_match_never_generates_option_one_label():
    state=domain.blank_state()
    main._begin_turn(state,'I am looking for a 2-bedroom house')
    result=domain.search_properties(state,{'bedrooms':2})
    assert result['ok'] and len(result['matching_properties'])==1
    actual=delivery_policy.validate_outbound('I found a 2-bedroom house H-201.',state)
    assert '\nH-201 -' in actual
    assert '\n1. ' not in actual
    domain.end_turn(state,actual)
    assert state['last_presented_property_options']['options']==[
        dict(display_number=1,unit_id='H-201',commercial_name='Two-Bedroom Garden House',price_usd=245000)]


def test_option_two_without_menu_is_ambiguous_and_does_not_look_up_old_shortlist():
    state=domain.blank_state()
    state['last_presented_units']=['H-201']
    state['shortlisted_unit_ids']=['H-201']
    domain.begin_turn(state,'Option 2 its ok for me')
    assert state['_turn_intent']=='property_selection'
    assert state['_turn_property_resolution']['ambiguous']
    assert state['conversation_focus']['unit_id'] is None
    assert state['visit_draft']['unit_id'] is None
    answer=delivery_policy.validate_outbound('Sure, scheduling H-201!',state)
    assert 'Which house' in answer


def test_new_search_replaces_menu_and_old_choice_is_not_reused():
    state,_=fresh_menu()
    domain.begin_turn(state,'Now show me homes with 2 bedrooms')
    assert state['_turn_intent']=='property_search'
    results=domain.search_properties(state)
    assert results['ok']
    answer=delivery_policy.validate_outbound('H-201 is available.',state)
    domain.end_turn(state,answer)
    assert [x['unit_id'] for x in state['last_presented_property_options']['options']]==['H-201']
    domain.begin_turn(state,'Option 2 its ok for me')
    assert state['_turn_property_resolution']['ambiguous']
    assert state['conversation_focus']['unit_id'] != 'H-101'


def test_formatted_menu_survives_plain_sms_segments():
    state,reply=fresh_menu()
    assert sms.normalize(reply)==reply
    segments=sms.outgoing_segments(reply)
    assert segments
    assert '\n' in '\n\n'.join(segments)
    whole='\n\n'.join(segments)
    assert '1. H-204' in whole and '2. H-101' in whole
    assert whole.index('1. H-204')<whole.index('2. H-101')


@pytest.mark.parametrize('buyer_text,selected',[
    ('Option 1', 'H-204'),
    ('the second one', 'H-101'),
    ('I choose the $320k house', 'H-101'),
])
def test_references_use_previous_visible_option_list(buyer_text,selected):
    state,_=fresh_menu()
    domain.begin_turn(state,buyer_text)
    assert state['_turn_property_resolution']['unit_id']==selected
    assert state['conversation_phase']=='property_details'
    assert state['visit_draft']['unit_id'] is None


def test_selection_preserves_existing_visit_draft_but_invalidates_stale_consent():
    state,_=fresh_menu()
    state['visit_draft'].update(unit_id='H-204',status='awaiting_confirmation',
                                date='2026-11-26',start_time='09:00')
    state['confirmation_request']={'unit_id':'H-204','date':'2026-11-26','start_time':'09:00'}
    domain.begin_turn(state,'Option 2 its ok for me')
    assert state['conversation_focus']['unit_id']=='H-101'
    assert state['conversation_phase']=='property_details'
    assert state['visit_draft']['unit_id']=='H-204'
    assert state['visit_draft']['date']=='2026-11-26'
    assert state['visit_planning_paused'] is True
    assert state['confirmation_request'] is None
