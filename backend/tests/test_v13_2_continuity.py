"""V13.2 regressions: anonymized dialogue paraphrases, fictional inventory only.

Source: two synthetic houses (H-201, H-204), a $200k/2-bed profile,
and questions about an 8 AM viewing during the following week.
No uploaded JSON, contact information or IDs are copied into the repository.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app import (commercial_turn, commercial_continuity as cc,
                 conversation_context, delivery_policy, domain, scheduling)
import main

LIMA=ZoneInfo('America/Lima')


def primary_state():
    state=domain.blank_state()
    main._begin_turn(state,'I am looking for a 2-bedroom house')
    domain.end_turn(state,'H-201 has two bedrooms.')
    main._begin_turn(state,"I don't want to spend more than $200k")
    domain.end_turn(state,'The lowest-priced alternative is H-201, above your budget.')
    return state


def interest_state():
    state=primary_state()
    main._begin_turn(state,'I am interested in H204. Please provide more information.')
    return state


@pytest.mark.parametrize('utterance',[
    'What about the other days that week?',
    'Which day next week could I visit at 8:00 a.m.?',
    'Are there any other dates or times?',
    'What about 8 AM on other days?',
])
def test_calendar_language_cannot_trigger_property_search(utterance):
    state=domain.blank_state()
    domain._set_focus(state,'H-204')
    domain._set_visit_unit(state,'H-204')
    state['conversation_phase']='visit_planning'
    assert cc.is_calendar_question(utterance,has_visit=True,phase='visit_planning')
    assert commercial_turn.intent(utterance,state)=='visit_planning'
    assert not commercial_turn.search_request(utterance)


@pytest.mark.parametrize('utterance',[
    'Show me other houses with three bedrooms',
    'Do you have another property with a lower price?',
    'Find a house with 3 bedrooms under $300k',
])
def test_real_property_search_still_triggers(utterance):
    assert commercial_turn.search_request(utterance)


def test_h204_and_h_204_are_the_same_verified_code():
    units=domain._units_by_code()
    assert conversation_context.mentioned_codes('I am interested in H204',units)==['H-204']
    assert conversation_context.mentioned_codes('Please show H-204',units)==['H-204']


def test_last_assistant_mentioned_h201_never_switches_active_h204_visit():
    state=domain.blank_state()
    domain._set_focus(state,'H-204')
    domain._set_visit_unit(state,'H-204')
    state['conversation_phase']='visit_planning'
    state['last_presented_units']=['H-201']
    state['last_assistant_reply']='H-201 is the lowest-priced option.'
    domain.begin_turn(state,'What about the other days that week?')
    assert state['_turn_intent']=='visit_planning'
    assert state['visit_draft']['unit_id']=='H-204'
    assert state['_turn_property_resolution']['unit_id']=='H-204'
    assert state['conversation_phase']=='visit_planning'
    assert state.get('active_search') is None


def test_buyer_explicit_new_visit_changes_house_but_not_inherited_slot():
    state=domain.blank_state()
    domain._set_focus(state,'H-204')
    domain._set_visit_unit(state,'H-204')
    state['visit_draft'].update(date='2026-11-26',start_time='15:00',status='awaiting_confirmation')
    state['last_presented_units']=['H-201']
    domain.begin_turn(state,'Actually, I would like to visit H-201 instead.')
    assert state['visit_draft']['unit_id']=='H-201'
    assert state['visit_draft']['date'] is None
    assert state['visit_draft']['start_time'] is None
    assert state['visit_draft']['preferred_date']=='2026-11-26'
    assert state['visit_draft']['preferred_start_time']=='15:00'
    assert state.get('confirmation_request') is None


def test_state_retains_explicit_interest_and_preference_advice_for_h204():
    state=interest_state()
    assert state['conversation_focus']['unit_id']=='H-204'
    assert state['lead']['interest_unit_id']=='H-204'
    assert state['property_interests'][0]['status']=='interested'
    proposal=state['preference_update_proposals']['H-204']
    assert proposal['status']=='pending'
    assert proposal['suggested_changes']['budget_maximum_usd']['excess_usd']==95000
    assert proposal['suggested_changes']['bedrooms']['current']==2
    assert state['search_preferences']['budget']['maximum_usd']==200000
    assert state['search_preferences']['bedrooms']['preferred']==2


def test_preference_advice_is_offered_once_without_blocking_details():
    state=interest_state()
    first=delivery_policy.validate_outbound('H-204 has 3 bedrooms and 135 sq m.',state)
    assert 'update those search preferences' in first
    assert state['preference_update_proposals']['H-204']['status']=='offered'
    again=delivery_policy.validate_outbound('H-204 has 3 bedrooms and 135 sq m.',state)
    assert 'update those search preferences' not in again
    assert state['search_preferences']['budget']['maximum_usd']==200000


def test_declining_preference_advice_preserves_the_original_cap():
    state=interest_state()
    domain.end_turn(state,delivery_policy.validate_outbound('H-204 has 135 sq m.',state))
    main._begin_turn(state,'Keep my original budget and consider H-204 as an exception.')
    assert state['preference_update_proposals']['H-204']['status']=='exception_only'
    assert state['search_preferences']['budget']['maximum_usd']==200000
    assert state['search_preferences']['bedrooms']['preferred']==2


def test_explicitly_accepting_offered_update_changes_only_confirmed_fields():
    state=interest_state()
    domain.end_turn(state,delivery_policy.validate_outbound('H-204 has 135 sq m.',state))
    main._begin_turn(state,'Yes, update both preferences for H-204.')
    assert state['search_preferences']['budget']['maximum_usd']==295000
    assert state['search_preferences']['bedrooms']['preferred']==3
    assert state['preference_update_proposals']['H-204']['status']=='accepted'
    assert state['search_preferences_history'][-1]['source_message_id']==state['_active_source_message_id']


def test_asking_to_visit_h204_does_not_raise_budget_automatically():
    state=primary_state()
    domain.begin_turn(state,'I would like to visit H-204.')
    assert state['visit_draft']['unit_id']=='H-204'
    assert state['search_preferences']['budget']['maximum_usd']==200000
    assert state['search_preferences']['bedrooms']['preferred']==2


def test_full_next_week_exact_8am_reports_all_period_not_prior_nov26(monkeypatch):
    monkeypatch.setattr(scheduling,'local_now',lambda: datetime(2026,11,21,15,30,tzinfo=LIMA))
    state=domain.blank_state()
    domain._set_focus(state,'H-204')
    domain._set_visit_unit(state,'H-204')
    domain.begin_turn(state,'Which day next week can I visit at 8:00 a.m.?')
    outcome=domain.slots(state,date_str='next week',requested_start_time='08:00')
    assert outcome['ok']
    assert outcome['status']=='no_slots_at_requested_time'
    assert outcome['unit_id']=='H-204'
    assert outcome['requested_period']=={'start':'2026-11-23','end':'2026-11-29'}
    assert outcome['earliest_available_start']=='09:00'
    assert outcome['available_dates']==[]
    assert state['visit_draft']['unit_id']=='H-204'
    assert state['visit_draft']['date'] is None
    assert state['visit_draft']['status']=='day_needed'


def test_accepting_9am_across_multiple_dates_only_stores_hour():
    state=domain.blank_state()
    domain._set_visit_unit(state,'H-204')
    state['last_offered_date']='2026-11-26'
    state['last_offered_dates']=['2026-11-23','2026-11-24','2026-11-25','2026-11-26']
    state['visit_draft']['date']='2026-11-26'
    domain.begin_turn(state,'9am its ok')
    result=domain.capture_explicit_visit_time(state,'9am its ok')
    assert not result['captured'] and result['day_needed']
    assert state['visit_draft']['unit_id']=='H-204'
    assert state['visit_draft']['preferred_start_time']=='09:00'
    assert state['visit_draft']['date'] is None
    assert not state.get('confirmation_request')


def test_unauthorized_maps_link_does_not_abort_location_turn():
    state=domain.blank_state()
    domain._set_focus(state,'H-204')
    domain.begin_turn(state,'First, I need the location of this property.')
    # Simulate a hallucinated exact pin: never expose it or throw ValueError.
    answer=delivery_policy.validate_outbound(
        'Find H-204 here: https://maps.google.com/?q=fake-private-house-pin',state)
    assert 'Lima' in answer and 'city-level' in answer
    assert 'fake-private-house-pin' not in answer


def test_unapproved_pdf_does_not_pass_as_approved_asset():
    state=domain.blank_state()
    domain.begin_turn(state,'Please send a PDF for H-204')
    answer=delivery_policy.validate_outbound(
        'Download https://untrusted.invalid/plan.pdf',state)
    assert 'untrusted.invalid' not in answer
    assert 'can share' in answer


def test_model_cannot_anchor_ambiguous_9am_to_a_stale_date(monkeypatch):
    monkeypatch.setattr(scheduling,'local_now',lambda: datetime(2026,11,21,15,30,tzinfo=LIMA))
    state=domain.blank_state()
    domain._set_visit_unit(state,'H-204')
    domain.begin_turn(state,'Are there any dates next week at 9am?')
    offered=domain.slots(state,date_str='next week',requested_start_time='09:00')
    assert offered['ok'] and len(offered['available_dates'])>1
    domain.end_turn(state,'For H-204, 9am is offered on several days next week.')
    domain.begin_turn(state,'9am its ok')
    preference=domain.capture_explicit_visit_time(state,'9am its ok')
    assert preference.get('day_needed')
    attempt=domain.slots(state,date_str='2026-11-26',requested_start_time='09:00')
    assert attempt['error_code']=='VISIT_DAY_REQUIRED'
    assert state['visit_draft']['date'] is None
    assert state['visit_draft']['unit_id']=='H-204'


def test_calendar_reply_cannot_redirect_h204_visit_to_h201():
    state=domain.blank_state()
    domain._set_visit_unit(state,'H-204')
    domain.begin_turn(state,'What about the other days that week?')
    reply=delivery_policy.validate_outbound(
        'You can visit H-201 on November 26 at 9 AM.',state)
    assert 'H-204' in reply
    assert 'H-201' not in reply
    assert state['visit_draft']['unit_id']=='H-204'
    assert any(o['tool']=='visit_house_reply_recovery' for o in state['tool_observations'])


def test_accepting_an_advised_area_change_updates_filter_with_evidence():
    state=primary_state()
    from app import property_filters
    existing=property_filters.update(state,{'min_area_sqm':150.0},'prior-message')
    assert existing['ok']
    main._begin_turn(state,'I am interested in H204; tell me more.')
    offer=state['preference_update_proposals']['H-204']
    assert offer['suggested_filter_changes']['min_area_sqm']['suggested']==135
    domain.end_turn(state,delivery_policy.validate_outbound('H-204 has 135 sq m.',state))
    main._begin_turn(state,'Yes, update all those preferences for H204.')
    assert state['search_preferences']['budget']['maximum_usd']==295000
    assert state['search_preferences']['bedrooms']['preferred']==3
    assert state['property_filters']['min_area_sqm']==135
    assert state['property_filters_history'][-1]['source_message_id']==state['_active_source_message_id']
