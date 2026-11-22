"""Contextual-choice regression suite: real domain/adapter, synthetic demo inventory.

No OpenRouter request, live SMS, real booking, or database mutation is needed.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import main
from app import contextual_choices as choices, delivery_policy, domain, scheduling, sms


def _freeze_calendar(monkeypatch):
    now=datetime(2026,11,21,12,0,tzinfo=ZoneInfo('America/Lima'))
    monkeypatch.setattr(scheduling,'local_now',lambda:now)
    slots=[{'date':day,'start_time':clock,'timezone':'America/Lima'}
           for day in ('2026-11-23','2026-11-24','2026-11-25','2026-11-26')
           for clock in ('09:00','10:00','11:00')]
    def verified_availability(state,*,date_filter=None,now=None,limit=None,
                              extra_occupied=None,include_today=False):
        filtered=[x for x in slots if date_filter is None or x['date']==date_filter.isoformat()]
        return filtered[:limit] if limit is not None else filtered
    monkeypatch.setattr(scheduling,'available_slots',verified_availability)


def _offered_days(monkeypatch):
    _freeze_calendar(monkeypatch)
    state=domain.blank_state()
    # An actual lead's visit request established H-201 beforehand.
    domain._set_visit_unit(state,'H-201')
    domain._set_focus(state,'H-201')
    domain.begin_turn(state,'On which days next week could I visit H-201?')
    response=domain.slots(state,date_str='next week')
    assert response['ok'] and len(response['available_dates'])==4
    text=delivery_policy.validate_outbound('November 23 or November 24?',state)
    assert '\n1. Monday, November 23' in text
    assert '\n2. Tuesday, November 24' in text
    assert '\n3. Wednesday, November 25' in text
    assert '\n4. Thursday, November 26' in text
    assert sms.outgoing_segments(text)==[text]
    domain.end_turn(state,text)
    snapshot=state['last_presented_choice_set']
    assert snapshot['type']=='visit_day'
    assert snapshot['options'][1]['date']=='2026-11-24'
    assert snapshot['source_reply_message_id']==state['bp_episode']['sales_messages'][-1]['id']
    return state


@pytest.mark.parametrize('buyer_text',['2','Option 2','the second one',
   'The second option its ok','la segunda','Tuesday','November 24th',
   'By "second option" I meant November 24th: 9 a.m. to 4 p.m.'])
def test_second_day_keeps_visit_house_and_not_property_selection(monkeypatch,buyer_text):
    state=_offered_days(monkeypatch)
    # Previous property shortlist cannot supersede the most recent day list.
    state['last_presented_property_options']={'options':[
        {'display_number':1,'unit_id':'H-204'},
        {'display_number':2,'unit_id':'H-101'}]}
    before_search=len(state['search_sessions'])
    main._begin_turn(state,buyer_text)
    assert state['_turn_intent']=='visit_day_selection'
    assert state['_turn_property_resolution']['unit_id']=='H-201'
    assert state['conversation_focus']['unit_id']=='H-201'
    assert state['visit_draft']['unit_id']=='H-201'
    assert state['visit_draft']['date']=='2026-11-24'
    assert state['visit_draft']['start_time'] is None
    assert state['visit_draft']['status']=='hour_needed'
    assert len(state['search_sessions'])==before_search
    assert state['booked_visits']==[]


def test_day_then_hour_then_confirmation_request_is_not_a_booking(monkeypatch):
    state=_offered_days(monkeypatch)
    main._begin_turn(state,'The second option')
    result=domain.slots(state,date_str='2026-11-24')
    assert result['ok'] and result['proposal_id']
    assert len(result['available_slots'])==3
    outbound=delivery_policy.validate_outbound('Tomorrow at 9 could work.',state)
    assert 'H-201' in outbound and 'November 24' in outbound
    assert '\n1. 9:00 AM' in outbound
    assert '\n2. 10:00 AM' in outbound
    domain.end_turn(state,outbound)
    assert state['last_presented_choice_set']['type']=='visit_time'
    main._begin_turn(state,'2')
    assert state['_turn_intent']=='visit_time_selection'
    assert state['visit_draft']['date']=='2026-11-24'
    assert state['visit_draft']['start_time']=='10:00'
    assert state['visit_draft']['status']=='awaiting_confirmation'
    assert state['booked_visits']==[]
    recap=delivery_policy.validate_outbound('Your booking is confirmed',state)
    assert 'not booked yet' in recap.lower()
    assert '10:00 AM' in recap
    domain.end_turn(state,recap)
    assert state['confirmation_request']['unit_id']=='H-201'
    assert state['last_presented_choice_set'] is None
    assert state['booked_visits']==[]


def test_out_of_range_ordinal_does_not_guess_property(monkeypatch):
    state=_offered_days(monkeypatch)
    main._begin_turn(state,'Option 9')
    assert state['_turn_intent']=='visit_day_selection'
    assert state['visit_draft']['date'] is None
    answer=delivery_policy.validate_outbound('Ok, booked!',state)
    assert 'option' in answer.lower() and 'house' not in answer.lower()
    assert state['booked_visits']==[]


def test_media_menu_is_numbered_from_verified_public_urls():
    menu=choices.media_menu('Would you like the photos or the PDF fact sheet?',
                            unit_id='H-201', media={
                                'gallery_url':'/properties/H-201/gallery',
                                'documents':[{'type':'fact_sheet','url':'/demo-assets/H201.pdf'}]})
    assert menu is not None
    text=choices.render_menu(menu)
    assert '1. Photos' in text and '2. PDF fact sheet' in text
    state={'last_presented_choice_set':choices.menu_record(menu,reply_message_id='media-msg'),
           'choice_set_initialized':True,'visit_draft':{'unit_id':None,'status':'idle'}}
    assert choices.resolve(state,'option 2')['choice']['kind']=='documents'
    assert choices.resolve(state,'photos please')['choice']['kind']=='gallery'


def test_single_choice_has_no_number_and_no_stale_fallback():
    menu=choices.choice_from_slots({'ok':True,'unit_id':'H-201',
        'date':'2026-11-24','proposal_id':'p','available_slots':[
            {'date':'2026-11-24','start_time':'09:00'}]})
    assert menu is not None
    assert '\n1. ' not in choices.render_menu(menu)
    state={'choice_set_initialized':True,'last_presented_choice_set':None,
           'last_presented_property_options':{'options':[{'display_number':2,'unit_id':'H-101'}]}}
    assert choices.resolve(state,'option 2') is None


def test_inline_next_actions_can_be_selected_after_single_listing():
    msg=('I found a 2-bedroom house:\n\nH-201 (2 beds, $245k)\n\n'
         'Would you like more details, or explore other options?')
    menu=choices.inline_choice_menu(msg)
    assert menu and menu['type']=='generic'
    actual=choices.render_menu(menu)
    assert 'H-201 (2 beds, $245k)' in actual
    assert '1. More details' in actual
    assert '2. Explore other options' in actual
    state={'choice_set_initialized':True,'last_presented_choice_set':choices.menu_record(menu,reply_message_id='m1')}
    assert choices.resolve(state,'option 2')['choice']['label']=='Explore other options'


def test_old_property_snapshot_still_resolves_when_no_new_choice_state():
    legacy={'last_presented_property_options':{'options':[
        {'display_number':1,'unit_id':'H-204'},
        {'display_number':2,'unit_id':'H-101'}]}}
    result=choices.resolve(legacy,'The second option')
    assert result['type']=='property' and result['choice']['unit_id']=='H-101'
