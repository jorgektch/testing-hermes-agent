"""Pure, dependency-free real estate business functions.

No production customer data is used. Appointments only exist in the caller's
signed, client-held demo session state. Nothing is persisted on Vercel disk.
"""
from __future__ import annotations

import calendar
import copy
import json
import os
import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from datetime import timezone
from . import scheduling, sales_preferences, conversation_context, property_filters, commercial_turn, search_sessions

PROJECT_TZ = ZoneInfo('America/Lima')
VISIT_MINUTES = 45
BUFFER_MINUTES = 15
SLOT_MINUTES = VISIT_MINUTES + BUFFER_MINUTES

# The 16 commercial profile keys described by BlackPenguin's current lead dictionary.
BP_COMMERCIAL_FIELDS = frozenset({
    'budget', 'budget_minimum', 'budget_maximum', 'budget_currency',
    'property_interest', 'bedrooms', 'bathrooms', 'location_preference',
    'purchase_timeline', 'financing', 'buyer_type', 'motivation',
    'decision_structure', 'move_in_timing', 'visit_readiness', 'needs',
})

CATALOG_PATH = Path(__file__).resolve().parents[2] / 'shared' / 'catalog.json'
BP_LEAD_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / 'shared' / 'blackpenguin' / 'lead.json'
if not BP_LEAD_TEMPLATE_PATH.is_file():
    BP_LEAD_TEMPLATE_PATH = Path(__file__).resolve().parent / 'lead.json'
# Vercel Services builds backend/ independently. A mirrored catalog is packaged
# under backend/app/ as well; choose it if the monorepo sibling isn't bundled.
if not CATALOG_PATH.is_file():
    CATALOG_PATH = Path(__file__).resolve().parent / 'catalog.json'


def now_local() -> datetime:
    return datetime.now(PROJECT_TZ)


def read_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def read_lead_template() -> dict:
    """Load the source-grounded BlackPenguin draft episode template."""
    return json.loads(BP_LEAD_TEMPLATE_PATH.read_text(encoding='utf-8'))


def blank_state() -> dict:
    bp = read_lead_template()
    lead_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4())
    bp['ids']['lead_id'] = lead_id
    bp['ids']['conversation_id'] = conversation_id
    bp['leads']['id'] = lead_id
    bp['sales_conversations']['id'] = conversation_id
    bp['sales_conversations']['lead_id'] = lead_id
    bp['sales_conversations']['continuity_json']['lead_id'] = lead_id
    bp['sales_dialogue_states']['conversation_id'] = conversation_id
    bp['sales_dialogue_states']['lead_id'] = lead_id
    bp['sales_booking_operations'] = []
    bp['sales_reschedule_operations'] = []
    bp['sales_cancellation_operations'] = []
    bp['sales_conversation_lead_contexts'] = [{
        'conversation_id': conversation_id, 'lead_id': lead_id,
        'is_active': True, 'activated_at': now_local().isoformat(),
    }]
    return {
        'lead': {
            'first_name': None, 'last_name': None, 'email': None, 'phone': None,
            'budget_usd': None, 'purchase_timeframe': None,
            'property_type': None, 'min_bedrooms': None,
            'interest_unit_id': None,
        },
        'stage': 'New Meta lead',
        'shortlisted_unit_ids': [],
        'exploring_unit_id': None,  # legacy UI projection
        'conversation_focus': {'project_id': bp['ids']['project_id'],
                               'unit_id': None, 'ambiguous': False,
                               'source_message_id': None},
        'visit_draft': {'unit_id': None, 'date': None, 'start_time': None, 'status': 'idle'},
        'last_slot_search': None,
        'last_offered_date': None,
        'booking_offers': {},
        'confirmation_request': None,
        'shared_media': {},
        'search_preferences': sales_preferences.empty(),
        'search_preferences_history': [],
        'active_search': None,
        'search_sessions': [],
        'visit_planning_paused': False,
        'offered_assets': {},
        'property_filters': property_filters.empty(),
        'property_filters_history': [],
        'booked_visits': [],
        'booking_proposal': None,
        'property_interests': [],
        'last_presented_units': [],
        'last_assistant_reply': None,
        'conversation_phase': 'discovery',
        'answered_topics': [],
        'conversation_transitions': [],
        'ui_fact_evidence': {},
        'tool_observations': [],
        'events': [],
        'turn_count': 0,
        'bp_episode': bp,
    }




def _preferences(state: dict) -> dict:
    if 'search_preferences' not in state:
        prefs=sales_preferences.empty()
        lead=state.get('lead') or {}
        if lead.get('budget_usd'):
            prefs['budget'].update(mode='cap', maximum_usd=int(lead['budget_usd']))
        if lead.get('min_bedrooms'):
            prefs['bedrooms'].update(mode='minimum', minimum=int(lead['min_bedrooms']))
        state['search_preferences']=prefs
    return state['search_preferences']


def update_search_preferences(state: dict, **kwargs) -> dict:
    """Revise commercial preferences only when supported by the buyer's message."""
    keys={'budget_minimum_usd':('budget','minimum_usd'),
          'budget_maximum_usd':('budget','maximum_usd'),
          'budget_target_usd':('budget','target_usd'),
          'budget_exact_price_usd':('budget','exact_price_usd'),
          'budget_mode':('budget','mode'),
          'bedrooms_min':('bedrooms','minimum'),
          'bedrooms_max':('bedrooms','maximum'),
          'bedrooms_preferred':('bedrooms','preferred'),
          'bedrooms_mode':('bedrooms','mode')}
    if set(kwargs)-set(keys):
        return {'error':'Unknown commercial preference.'}
    message=str(state.get('_active_source_message_text') or '')
    if state.get('_turn_scope')=='temporary':
        return {'ok':True,'changed_fields':[],
                'scope':'temporary','note':'Use search_properties for this exploration; permanent preferences unchanged.'}
    recognized=sales_preferences.parse_declarations(message)
    requested={}
    for name,value in kwargs.items():
        group,field=keys[name]
        requested.setdefault(group,{})[field]=value
    if message and requested:
        # Only declared preference groups may change. Hermes can restate a
        # saved preference unchanged, but cannot silently revise it.
        for group,passed in list(requested.items()):
            declared=recognized.get(group)
            if declared:
                for field,value in passed.items():
                    if field in declared and value!=declared[field]:
                        return {'error':'Preference disagrees with buyer statement.',
                                'error_code':'PREFERENCE_CONFLICT'}
                requested[group]=declared
                continue
            current=(_preferences(state).get(group) or {})
            if all(current.get(field)==value for field,value in passed.items()):
                requested.pop(group)
            else:
                return {'error':'Preference change lacks buyer evidence.',
                        'error_code':'PREFERENCE_NOT_DECLARED',
                        'next_action':'Reuse saved preferences; only write explicitly revised criteria.'}
    if not requested:
        return {'ok':True,'changed_fields':[],
                'search_preferences':_preferences(state)}
    result=sales_preferences.apply(state, requested,
        source_message_id=state.get('_active_source_message_id'))
    if not result.get('ok') or not result['changed_fields']:
        return result
    prefs=state['search_preferences']
    facts={}
    if 'budget' in result['changed_fields']:
        b=prefs['budget']
        if b['minimum_usd'] is not None:facts['budget_minimum']=b['minimum_usd']
        if b['maximum_usd'] is not None:facts['budget_maximum']=b['maximum_usd']
        facts['budget']=(f"USD {b['target_usd']} ({b['mode']})" if b['target_usd'] else
            f"USD {b['exact_price_usd']} (exact listing price)" if b['exact_price_usd'] else
            f"USD {b['minimum_usd'] or 'unset'} to {b['maximum_usd'] or 'unset'} ({b['mode']})")
        ledger=state['bp_episode']['leads']['lead_profile_data']['sales_profile']
        for key in ('budget_minimum','budget_maximum'):
            if key not in facts and key in ledger['current']:
                old=ledger['current'].pop(key)
                old['status']='superseded'
                ledger['history'].append(old)
        state.setdefault('ui_fact_evidence',{})['budget_usd']={
            'source_message_id':state.get('_active_source_message_id'),
            'evidence':message,'captured_at':now_local().isoformat(),
            'source':'active_conversation'}
    if 'budget' in result['changed_fields'] and state.get('property_filters'):
        # An explicit new budget replaces older relative-price exploration;
        # the current message may establish a fresh comparison afterward.
        property_filters.update(state,{
            'relative_price_lt':None,'relative_price_gt':None,
            'source_unit_id':None},state.get('_active_source_message_id'))
    if 'bedrooms' in result['changed_fields']:
        b=prefs['bedrooms']
        facts['bedrooms']=next(n for n in (b['preferred'],b['minimum'],b['maximum']) if n is not None)
    _capture_bp_facts(state,facts)
    state['stage']='Preferences updated'
    record(state,'preferences_updated',
           f"Revised {', '.join(result['changed_fields'])} (revision {prefs['revision']})")
    return result


def _selected_offer(state: dict) -> dict | None:
    draft=_draft(state)
    offer_id=draft.get('proposal_id')
    if offer_id:
        return ((state.get('booking_offers') or {}).get(offer_id) or
                (state.get('booking_proposal') or {}))
    p=state.get('booking_proposal') or {}
    if p.get('selected_start_time')==draft.get('start_time') and p.get('selected_date')==draft.get('date'):
        return p
    return None


def _resolve_visit_date(state: dict, raw: str) -> str:
    if raw.strip().lower() in ('the following day','following day','next day',
                                'the next day','day after'):
        anchor=state.get('last_offered_date')
        if not anchor:
            raise ValueError('Which date do you mean by the following day?')
        return (date.fromisoformat(anchor)+timedelta(days=1)).isoformat()
    return date_input(raw)


def _units_by_code() -> dict[str, dict]:
    return {u['unit_id']: u for u in read_catalog()['properties']}


def _focus(state: dict) -> dict:
    """Restore the explicit topic scope for historical lead states."""
    focus = state.setdefault('conversation_focus', {
        'project_id': state['bp_episode']['ids']['project_id'],
        'unit_id': state.get('exploring_unit_id'), 'ambiguous': False,
        'source_message_id': None,
    })
    focus.setdefault('project_id', state['bp_episode']['ids']['project_id'])
    focus.setdefault('unit_id', state.get('exploring_unit_id'))
    focus.setdefault('ambiguous', False)
    return focus


def _draft(state: dict) -> dict:
    """Migrate an older saved appointment offer without changing the appointment."""
    if 'visit_draft' not in state:
        proposal = state.get('booking_proposal') or {}
        code = proposal.get('selected_unit_id')
        if code in _units_by_code():
            state['visit_draft'] = {
                'unit_id': code, 'date': proposal.get('selected_date') or proposal.get('date'),
                'start_time': proposal.get('selected_start_time'),
                'status': ('confirmed' if proposal.get('status') == 'confirmed' else
                           'awaiting_confirmation' if proposal.get('selected_start_time') else 'hour_needed')}
        else:
            state['visit_draft'] = {'unit_id': None, 'date': None, 'start_time': None, 'status': 'idle'}
    return state['visit_draft']


def _set_focus(state: dict, code: str, source_message_id: str | None = None) -> None:
    unit = _units_by_code().get(code)
    if not unit:
        return
    focus = _focus(state)
    focus.update(project_id=state['bp_episode']['ids']['project_id'],
                 unit_id=code, ambiguous=False,
                 source_message_id=source_message_id or state.get('_active_source_message_id'))
    state['exploring_unit_id'] = code
    bp = state['bp_episode']
    bp['ids']['unit_id'] = unit['blackpenguin_unit_id']
    bp['ids']['property_type_id'] = unit['property_type_id']
    dialogue = bp['sales_dialogue_states']['state_json']
    dialogue['selected_unit_id'] = unit['blackpenguin_unit_id']
    dialogue['selected_property_type_id'] = unit['property_type_id']
    dialogue['conversation_focus'] = copy.deepcopy(focus)


def _set_visit_unit(state: dict, code: str) -> None:
    if code not in _units_by_code():
        return
    state['visit_planning_paused']=False
    draft = _draft(state)
    if draft.get('unit_id') != code:
        draft.update(unit_id=code, date=None, start_time=None,proposal_id=None,status='day_needed')
        state['confirmation_request']=None
        proposal = state.get('booking_proposal')
        if proposal and proposal.get('selected_unit_id') != code and proposal.get('status') == 'offered':
            state['booking_proposal'] = None
            state['bp_episode']['sales_dialogue_states']['state_json']['booking_proposal'] = None
    elif draft.get('status') == 'idle':
        draft['status'] = 'day_needed'
    state['bp_episode']['sales_dialogue_states']['state_json']['visit_draft'] = copy.deepcopy(draft)


def _observe_inbound_context(state: dict, message: str) -> None:
    """Resolve topic references without treating exploration as booking consent.

    The buyer can ask for public details of the sole H-201 we just recommended,
    without ever saying "H-201" or declaring interest. Booking is independent.
    """
    known=_units_by_code()
    resolution=conversation_context.resolved_reference(state,message,known)
    state['_turn_property_resolution']=resolution
    named=conversation_context.mentioned_codes(message,known)
    discussed=(state.get('_turn_intent')=='property_details' and
               bool(conversation_context.is_detail_question(message)))
    exploration_yes=conversation_context.is_property_confirmation(
        message,str(state.get('last_assistant_reply') or ''))
    visit_intent=bool(re.search(
        r'\b(?:visit|tour|book|schedule|appointment|slots|visit times|'
        r'which hours|what times|see in person|view in person|'
        r'see\s+H-\d{3}|view\s+H-\d{3})\b',message,re.I))
    negated=bool(re.search(
        r"\b(?:don['’]?t|do not|not|cancel|without)\s+(?:want to )?"
        r"(?:visit|tour|book|schedule)\b",message,re.I))
    interest_declared=bool(re.search(
        r'\b(?:i(?: am|m|\'m)? interested|i (?:want|like|prefer|choose|pick) '
        r'(?:that|this|the) (?:house|one|property)|i prefer that one)\b',
        message,re.I))
    affirmative_only=bool(re.fullmatch(
        r'\s*(?:yes|yes please|sure|certainly|of course)[.!]?\s*',
        message,re.I))
    pending_booking=bool(state.get('confirmation_request'))
    should_focus=((len(named)==1 and state.get('_turn_intent') not in
                  ('property_search','property_comparison')) or
                  (affirmative_only and not pending_booking and
                   resolution.get('unit_id') and not resolution.get('ambiguous')) or
                  (resolution.get('unit_id') and (
                      discussed or exploration_yes or interest_declared or
                      visit_intent or resolution.get('reason') in ('ordinal','comparative'))))
    if state.get('_turn_intent') in ('property_search','preference_update'):
        state['conversation_phase']='exploration'
        state['stage']='Exploring property alternatives'
        state['visit_planning_paused']=bool(_draft(state).get('unit_id') and
                                         _draft(state).get('status') not in ('confirmed','idle'))
        # A prior request to confirm a visit cannot survive an intervening
        # search as a standing consent. The slot remains a draft.
        state['confirmation_request']=None
        state['pending_visit_offer']=None
        return
    elif should_focus and resolution.get('unit_id'):
        _set_focus(state,resolution['unit_id'])
        state['conversation_phase']='property_details' if discussed or exploration_yes else state.get('conversation_phase','discovery')
        if exploration_yes or interest_declared:
            state['stage']='Exploring property details'
    elif resolution.get('ambiguous') and (discussed or exploration_yes or visit_intent):
        state['_turn_property_ambiguous']=True
    offer=state.pop('pending_visit_offer',None)
    affirmative=bool(re.fullmatch(
        r'\s*(?:yes|yes please|sure|please do|that works|sounds good|ok|okay)[.!]?\s*',
        message,re.I))
    if (affirmative and offer and not named and
            offer.get('unit_id') in known):
        _set_visit_unit(state,offer['unit_id'])
        state['conversation_phase']='visit_planning'
        return
    existing_visit_reference=bool(re.search(
        r'\b(?:confirm|go ahead|proceed|finalize|reschedule|cancel)\b|'
        r'\b(?:my|our|the)\s+appointment\b|\bthe visit we discussed\b',
        message,re.I))
    if existing_visit_reference and not named and _draft(state).get('unit_id'):
        return
    active=next((b for b in state.get('booked_visits',[])
                 if b.get('status')=='confirmed'),None)
    if active and re.search(
        r'\b(?:reschedule|change|move|postpone|switch)\b.*'
        r'\b(?:visit|appointment|date|day|time)\b',message,re.I):
        draft=_draft(state)
        draft['action']='reschedule'
        draft['original_meeting_id']=active['meeting_id']
    if visit_intent and not negated and resolution.get('unit_id') and not resolution.get('ambiguous'):
        _set_visit_unit(state,resolution['unit_id'])
        state['conversation_phase']='visit_planning'

def _slot_error(code: str, message: str, unit_id: str | None = None,
                *, next_action: str, **details) -> dict:
    return {'ok': False, 'error_code': code, 'error': message,
            'unit_id': unit_id, 'next_action': next_action, **details}


def _time_matches(clock: str, preference: str | None) -> bool:
    hour = int(clock[:2])
    return (preference in (None, '', 'any') or
            (preference == 'morning' and hour < 12) or
            (preference == 'afternoon' and 12 <= hour < 17) or
            (preference == 'evening' and hour >= 17))


def _relative_range(raw: str, today: date) -> tuple[date, date] | None:
    value = str(raw).strip().lower()
    if value in ('next week', 'the next week'):
        monday = today + timedelta(days=7-today.weekday())
        return monday, monday + timedelta(days=6)
    if value in ('this week', 'later this week'):
        return today + timedelta(days=1), today + timedelta(days=6-today.weekday())
    if value in ('this weekend', 'the weekend'):
        saturday = today + timedelta(days=(5-today.weekday()) % 7)
        if saturday <= today:
            saturday += timedelta(days=7)
        return saturday, saturday + timedelta(days=1)
    if value == 'next weekend':
        saturday = today + timedelta(days=(5-today.weekday()) % 7 + 7)
        return saturday, saturday + timedelta(days=1)
    return None


def record(state: dict, event: str, label: str) -> None:
    state.setdefault('events', []).append({
        'id': str(uuid.uuid4())[:8], 'event': event,
        'label': label, 'timestamp': now_local().isoformat(timespec='seconds'),
    })
    state['events'] = state['events'][-20:]


def begin_turn(state: dict, message: str) -> None:
    """Record the inbound simulated message with the active lead scope."""
    bp = state['bp_episode']
    mid = str(uuid.uuid4())
    state['_active_source_message_id'] = mid
    state['_active_source_message_text'] = message
    state['_turn_intent']=commercial_turn.intent(message)
    state['_turn_scope']=commercial_turn.scope(message,search_sessions.has_main_preferences(state))
    state['_last_inbound_topic']=commercial_turn.core_query_topic(message)
    _observe_inbound_context(state, message)
    bp['sales_messages'].append({
        'id': mid, 'conversation_id': bp['ids']['conversation_id'],
        'role': 'user', 'direction': 'inbound', 'channel': 'simulation',
        'content': message, 'status': 'received',
        'metadata_json': {'lead_id': bp['ids']['lead_id'], 'project_id': bp['ids']['project_id']},
        'created_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
    })
    bp['sales_conversations']['continuity_json']['last_inbound_message_id'] = mid
    bp['sales_conversations']['continuity_json']['waiting_for_lead'] = False
    bp['sales_conversations']['stage'] = 'contacted'
    bp['leads']['funnel_stage'] = 'contacted' if not state['booked_visits'] else 'appointment_set'



def _remember_answered_topics(state: dict, reply: str) -> None:
    """Record information actually delivered, not merely fetched by a tool."""
    topic=(state.get('_last_inbound_topic') or state.get('_pending_factual_topic_override'))
    if state.get('_turn_intent') in ('property_search','property_comparison'):
        topic=None
    resolution=state.get('_turn_property_resolution') or {}
    unit_id=resolution.get('unit_id') or _focus(state).get('unit_id')
    details=state.get('_details_for_current_turn') or {}
    if (topic in ('area','bathrooms','bedrooms','kitchen','parking','backyard',
                  'price','location','availability') and
            unit_id in _units_by_code() and
            details.get('unit_id')==unit_id and not resolution.get('ambiguous')):
        unit=_units_by_code()[unit_id]
        expected={
            'area':[str(unit.get('area_sqm')),str(unit.get('lot_area_m2'))],
            'bathrooms':[str(unit.get('bathrooms'))],
            'bedrooms':[str(unit.get('bedrooms'))],
            'price':[str(unit.get('price_usd')),f"{int(unit.get('price_usd')):,}"],
            'parking':[str(unit.get('confirmed_features',{}).get('parking_spaces'))],
            'backyard':[str(unit.get('confirmed_features',{}).get('backyard_area_m2'))],
            'kitchen':[str(unit.get('confirmed_features',{}).get('kitchen_layout'))],
            'location':[str((unit.get('location') or {}).get('city'))],
            'availability':[str(unit.get('available_from_date'))],
        }.get(topic,[])
        if any(x and x!='None' and x.lower() in reply.lower() for x in expected):
            record_={'unit_id':unit_id,'topic':topic,
                     'source_message_id':state.get('_active_source_message_id'),
                     'reply_message_id':state['bp_episode']['sales_messages'][-1]['id'],
                     'recorded_at':now_local().isoformat()}
            items=state.setdefault('answered_topics',[])
            items.append(record_)
            state['answered_topics']=items[-80:]
    dialogue=state['bp_episode']['sales_dialogue_states']['state_json']
    memory=dialogue.setdefault('memory',{})
    memory['answered_topics']=copy.deepcopy(state.get('answered_topics',[])[-40:])
    memory['recent_turns']=(memory.get('recent_turns',[])+[{
        'inbound_id':state.get('_active_source_message_id'),
        'inbound_topic':topic,'unit_id':unit_id,
        'assistant_reply_id':state['bp_episode']['sales_messages'][-1]['id'],
        'phase':state.get('conversation_phase'),
    }])[-12:]
    memory['as_of_message_id']=state['bp_episode']['sales_messages'][-1]['id']
    state['conversation_transitions']=(state.get('conversation_transitions',[])+[{
        'message_id':state.get('_active_source_message_id'),
        'unit_id':unit_id,'phase':state.get('conversation_phase'),
        'topic':topic,
    }])[-35:]


def end_turn(state: dict, reply: str) -> None:
    """Record the assistant response and remove transient source text."""
    bp = state['bp_episode']
    bp['sales_messages'].append({
        'id': str(uuid.uuid4()), 'conversation_id': bp['ids']['conversation_id'],
        'role': 'assistant', 'direction': 'outbound', 'channel': 'simulation',
        'content': reply, 'status': 'simulated',
        'metadata_json': {'lead_id': bp['ids']['lead_id'], 'project_id': bp['ids']['project_id']},
        'created_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
    })
    bp['sales_conversations']['continuity_json']['waiting_for_lead'] = True
    _remember_answered_topics(state, reply)
    new_mentions=conversation_context.presented_units(reply,_units_by_code())
    if new_mentions:
        state['last_presented_units']=new_mentions
    state['last_assistant_reply']=reply
    state.pop('_active_source_message_id', None)
    state.pop('_active_source_message_text', None)
    state.pop('_turn_intent', None)
    state.pop('_turn_scope', None)
    state.pop('_search_tool_called_this_turn', None)
    state.pop('_search_result_cache_this_turn', None)
    state.pop('_search_out_of_budget_this_turn', None)
    state.pop('_search_nearest_out_of_budget_this_turn', None)
    state.pop('_automatic_search_recovery', None)
    state.pop('_pref_updated_search_this_turn', None)
    # A simple explicit single-media offer can be accepted with "yes" next turn.
    # A combined "photos or visit?" question is ambiguous and never preauthorized.
    text = reply.lower()
    focused = state.get('exploring_unit_id') or state['lead'].get('interest_unit_id')
    photo_offer = bool(re.search(r'\b(photos?|pictures?|gallery|images?)\b', text))
    pdf_offer = bool(re.search(r'\b(pdf|brochures?|fact.?sheets?|floor.?plans?)\b', text))
    multiple_choices = bool(re.search(r'\b(visit|tour|book|appointment|schedule)\b', text))
    if focused and '?' in reply and photo_offer != pdf_offer and not multiple_choices:
        state['pending_media_offer'] = {'unit_id': focused,
                                        'kind': 'gallery' if photo_offer else 'documents'}
    else:
        state['pending_media_offer'] = None
    state.pop('_approved_media_urls_this_turn', None)
    state.pop('_media_resend_urls_this_turn', None)
    state.pop('_tool_rejection_cache', None)
    state.pop('_turn_property_ambiguous', None)
    state.pop('_turn_property_resolution', None)
    state.pop('_fact_answer_repaired_this_turn', None)
    state.pop('_matching_options_this_turn', None)
    state.pop('_details_for_current_turn', None)
    state.pop('_last_inbound_topic', None)
    state.pop('_pending_factual_topic_override', None)
    state.pop('_pending_fact_hint', None)
    state.pop('_slot_queries_this_turn', None)
    state.pop('_booking_completion_error', None)
    state.pop('_allowed_alternative_units_this_turn', None)
    mentioned = list(dict.fromkeys(code.upper() for code in re.findall(
        r'(?<![A-Z0-9])H-\d{3}(?![A-Z0-9])', reply.upper())))
    candidate = (mentioned[0] if len(mentioned) == 1 else
                 _focus(state).get('unit_id') if not mentioned else None)
    invitation = bool(re.search(r'\b(?:visit|tour|schedule|appointment)\b', reply, re.I))
    mixed = bool(re.search(r'\b(?:photos?|gallery|images?|pdf|brochure|or choose|or see)\b', reply, re.I))
    state['pending_visit_offer'] = (
        {'unit_id': candidate} if candidate and '?' in reply and invitation and not mixed else None)
    draft=_draft(state)
    if (draft.get('status')=='awaiting_confirmation' and
            re.search(r'\b(?:confirm|accept|reserve|book\s+it)\b',reply,re.I) and
            ('?' in reply or re.search(r'\b(?:reply|please confirm|can you confirm)\b',reply,re.I))):
        state['confirmation_request']={
            'unit_id':draft['unit_id'],'date':draft['date'],
            'start_time':draft['start_time'],
            'proposal_id':draft.get('proposal_id'),
            'prompt_message_id':bp['sales_messages'][-1]['id'],
        }


def _capture_bp_facts(state: dict, changed: dict) -> None:
    """Capture explicitly stated facts with BlackPenguin-style evidence envelopes.

    The UI's nine fields are a convenience projection; the source-grounded
    sales_profile.current/history is the fact ledger, not an eligibility score.
    """
    bp = state['bp_episode']
    record_ = bp['leads']
    sales = record_['lead_profile_data']['sales_profile']
    source_ref={'message_id':state.get('_active_source_message_id')}
    evidence=state.get('_active_source_message_text') or 'Captured through demo tool call (no verbatim message available).'
    fields = {
        'budget_usd': ('budget', lambda v: f'USD {v}'),
        'purchase_timeframe': ('purchase_timeline', str),
        'min_bedrooms': ('bedrooms', int),
        'interest_unit_id': ('property_interest', str),
        **{key: (key, lambda v: v) for key in BP_COMMERCIAL_FIELDS},
    }
    recorded = set()
    for key, val in changed.items():
        if key in ('first_name','last_name'):
            # Persist only if both are explicitly provided; the real Lead table
            # requires full_name, but the draft may still be incomplete.
            l=state['lead']
            if l.get('first_name') and l.get('last_name'):
                record_['full_name']=f"{l['first_name']} {l['last_name']}"
        elif key in ('email','phone'):
            record_[key]=val
        elif key == 'property_type':
            # This demo-only UI preference is not one of the 16 canonical fields.
            record_['lead_profile_data'].setdefault('optional_profile',{})['requested_property_category']=val
        if key not in fields:
            continue
        field, convert=fields[key]
        if field in recorded:
            continue
        recorded.add(field)
        previous=sales['current'].get(field)
        fact={
            'id':str(uuid.uuid4()), 'field':field, 'value':convert(val),
            'status':'declared', 'source':'active_conversation',
            'captured_at':now_local().isoformat(), 'evidence':evidence,
            'source_ref':dict(source_ref),'polarity':'affirmative',
        }
        if field=='buyer_type':
            record_['buyer_type']=str(val)
        if field=='property_interest':
            unit = next((u for u in read_catalog()['properties'] if u['unit_id']==val),None)
            if unit:
                fact['source_ref']['unit_id']=unit['blackpenguin_unit_id']
                fact['source_ref']['property_type_id']=unit['property_type_id']
                bp['ids']['unit_id']=unit['blackpenguin_unit_id']
                bp['ids']['property_type_id']=unit['property_type_id']
                bp['sales_dialogue_states']['state_json']['selected_property_type_id']=unit['property_type_id']
                bp['sales_dialogue_states']['state_json']['selected_unit_id']=unit['blackpenguin_unit_id']
        if previous:
            fact['supersedes']=previous['id']
            sales['history'].append(previous)
            sales['history']=sales['history'][-100:]
        sales['current'][field]=fact
    # Distinguish expressed interest from general discussion or model recommendations.
    if changed.get('interest_unit_id'):
        track_property_interest(state, changed['interest_unit_id'], status='interested')
    # The legacy facts mirror exists in the BP contract for compatibility.
    sales['facts'] = {k:v['value'] for k,v in sales['current'].items() if v['status']=='declared'}
    qualification=record_['lead_profile_data']['active_qualification']
    qualification['missing']=[k for k in ('budget','purchase_timeline') if k not in sales['current']]
    record_['qualification_summary']='; '.join(f'{k}: {v}' for k,v in sales['facts'].items()) or None
    bp['sales_dialogue_states']['revision']+=1
    if not qualification['missing']:
        record_['pipeline_stage']='S02_QUALIFICATION'
        record_['funnel_stage']='qualified'


def parse_target_date(raw: str | None) -> tuple[date | None, str]:
    """Conservatively interpret a lead's intended purchase deadline."""
    if not raw or not str(raw).strip():
        return None, 'Not provided'
    txt = str(raw).strip().lower().replace(',', ' ')
    today = now_local().date()
    if txt in ('asap', 'now', 'immediately', 'immediate'):
        return today, today.isoformat()
    if txt == 'this week':
        target = today + timedelta(days=7)
        return target, target.isoformat()
    if txt in ('next month', 'the next month'):
        serial = today.year * 12 + today.month + 1
        year, month0 = divmod(serial, 12)
        target = date(year, month0 + 1, 1) - timedelta(days=1)
        return target, target.isoformat()
    if txt in ('this year', 'by end of year'):
        return date(today.year, 12, 31), f'{today.year}-12-31'
    if txt in ('next year', 'by next year'):
        return date(today.year + 1, 12, 31), f'{today.year + 1}-12-31'
    found = re.search(r'\b(\d{1,3})\s*(days?|weeks?|months?|years?)\b', txt)
    if found:
        quantity = int(found.group(1))
        unit = found.group(2)
        if quantity > 120:
            return None, 'Please clarify the timeframe'
        if unit.startswith('day'):
            target = today + timedelta(days=quantity)
        elif unit.startswith('week'):
            target = today + timedelta(weeks=quantity)
        else:
            months = quantity * (12 if unit.startswith('year') else 1)
            serial = today.year * 12 + today.month - 1 + months
            year, month0 = divmod(serial, 12)
            target = date(year, month0 + 1, min(today.day, calendar.monthrange(year, month0 + 1)[1]))
        return target, target.isoformat()
    try:
        target = date.fromisoformat(txt)
        return target, target.isoformat()
    except ValueError:
        pass
    if re.fullmatch(r'20\d{2}-\d{2}', txt):
        year, month = map(int, txt.split('-'))
        if 1 <= month <= 12:
            target = date(year, month, calendar.monthrange(year, month)[1])
            return target, target.isoformat()
    quarter = re.fullmatch(r'q([1-4])\s*(20\d{2})', txt)
    if quarter:
        q, year = map(int, quarter.groups())
        month = q * 3
        target = date(year, month, calendar.monthrange(year, month)[1])
        return target, target.isoformat()
    for month, name in enumerate(calendar.month_name):
        if name and re.fullmatch(rf'{name.lower()}\s+20\d{{2}}', txt):
            year = int(txt.split()[-1])
            target = date(year, month, calendar.monthrange(year, month)[1])
            return target, target.isoformat()
    return None, 'Unclear; ask for a month/year or YYYY-MM-DD'


def ready_date(unit: dict) -> date:
    value = unit['available_from_date']
    return now_local().date() if value == 'Immediate' else date.fromisoformat(value)


def project_overview() -> dict:
    return {'ok': True, 'project_info': read_catalog()['project_info']}


def evaluate_properties(state: dict, max_budget_usd: int | None = None,
                        target_purchase_timeframe: str | None = None,
                        min_bedrooms: int | None = None,
                        preferred_property_type: str | None = None,
                        required_features: dict | None = None,
                        *, _preferences_override: dict | None = None,
                        _filters_override: dict | None = None,
                        _bedrooms_any: list[int] | None = None) -> dict:
    """Rank literal buyer matches. Never silently relax a declared constraint."""
    lead = state.get('lead', {})
    prefs = copy.deepcopy(_preferences_override if _preferences_override is not None
                          else _preferences(state))
    try:
        fallback_max = int(max_budget_usd) if max_budget_usd is not None else None
        if fallback_max is not None and fallback_max <= 0:
            raise ValueError
        fallback_beds = int(min_bedrooms) if min_bedrooms is not None else None
        if fallback_beds is not None and not 1 <= fallback_beds <= 20:
            raise ValueError
    except (ValueError, TypeError):
        return {'error': 'Invalid maximum budget or bedroom criteria.'}
    # Explicit current state wins over stale model arguments, including a
    # newly increased maximum. A soft target is not a hard spending limit.
    if prefs['budget']['mode'] == 'unknown':
        cap = lead.get('budget_usd') or fallback_max
        if cap is not None:
            prefs['budget'].update(mode='cap', maximum_usd=int(cap))
    if prefs['bedrooms']['mode'] == 'unknown':
        beds = lead.get('min_bedrooms') or fallback_beds
        if beds is not None:
            prefs['bedrooms'].update(mode='minimum', minimum=int(beds))
    allowed_types = {'house':'House','apartment':'Apartment','either':None}
    chosen = preferred_property_type or lead.get('property_type') or 'Either'
    chosen_type = allowed_types.get(str(chosen).strip().lower(), 'invalid')
    if chosen_type == 'invalid':
        return {'error':'Preferred property type must be House, Apartment or Either.'}
    features = required_features or {}
    refinements=(_filters_override if _filters_override is not None
                 else state.get('property_filters') or property_filters.empty())
    if not isinstance(features, dict):
        return {'error':'Required features must be a JSON object.'}
    allowed_features = {'kitchen_layout','private_backyard','backyard_area_m2',
                        'private_terrace','parking_spaces'}
    if set(features)-allowed_features:
        return {'error':'Unsupported confirmed-feature filter.'}
    try:
        for key,value in features.items():
            if key in ('parking_spaces','backyard_area_m2'):
                float(value)
            elif key in ('private_backyard','private_terrace') and not isinstance(value,bool):
                raise ValueError
            elif key=='kitchen_layout' and value not in ('open','closed','semi_open'):
                raise ValueError
    except (ValueError, TypeError):
        return {'error':'Invalid confirmed-feature filter value.'}
    deadline, interpretation = parse_target_date(
        target_purchase_timeframe or lead.get('purchase_timeframe'))
    primary, alternatives, out_budget, other = [], [], [], []
    for raw in read_catalog()['properties']:
        unit=copy.deepcopy(raw)
        unit['ready_by_purchase_deadline']=None if deadline is None else ready_date(unit)<=deadline
        eligible, violations, deviations=sales_preferences.filter_unit(unit,prefs)
        violations.extend(property_filters.matches(unit,refinements))
        if _bedrooms_any is not None and unit['bedrooms'] not in _bedrooms_any:
            violations.append('Bedroom count not among the requested alternatives')
        if chosen_type and unit['property_type']!=chosen_type:
            violations.append('Different property category')
        if any(
            key not in unit.get('confirmed_features',{}) or
            (float(unit['confirmed_features'][key])!=float(want) if key in ('parking_spaces','backyard_area_m2')
             else unit['confirmed_features'][key]!=want)
            for key,want in features.items()
        ):
            violations.append('Missing a required verified feature')
        if deadline is not None and ready_date(unit)>deadline:
            violations.append('Not ready by the stated purchase deadline')
        if violations:
            unit['mismatch_reason']='; '.join(violations)
            if 'Exceeds the declared maximum budget' in violations:
                cap=prefs['budget']['maximum_usd']
                if cap is not None:unit['exceeds_budget_by_usd']=unit['price_usd']-cap
                out_budget.append(unit)
            else:
                other.append(unit)
        elif deviations:
            unit['mismatch_reason']='; '.join(deviations)
            unit['alternative_only']=True
            alternatives.append(unit)
        else:
            primary.append(unit)
    target_beds=(prefs['bedrooms'].get('preferred') or prefs['bedrooms'].get('minimum') or 0)
    target_price=prefs['budget'].get('target_usd') or 0
    def order(u):
        return (abs(u['bedrooms']-target_beds) if target_beds else 0,
                abs(u['price_usd']-target_price) if target_price else u['price_usd'],
                u['unit_id'])
    primary.sort(key=order)
    alternatives.sort(key=order)
    out_budget.sort(key=lambda u:(u.get('exceeds_budget_by_usd',0),u['unit_id']))
    state['shortlisted_unit_ids']=[u['unit_id'] for u in primary]
    state['stage']='Properties matched' if primary else 'Exploring alternatives'
    if state.get('_turn_intent') in ('property_search','preference_update'):
        state['conversation_phase']='exploration'
    record(state,'matching',f"{len(primary)} exact matches; {len(alternatives)} optional alternatives")
    def compact(u):
        fields=('unit_id','commercial_name','property_type','bedrooms',
                'price_usd','available_from_date','status',
                'ready_by_purchase_deadline','exceeds_budget_by_usd',
                'mismatch_reason','alternative_only')
        return {k:u[k] for k in fields if k in u}
    spoken=str(state.get('_active_source_message_text') or '')
    alternatives_requested=bool(re.search(
        r'\b(?:alternative|alternatives|other options|other houses|anything else|'
        r'what else|more options|more bedrooms|fewer bedrooms|less bedrooms|'
        r'more area|larger|bigger|smaller|cheaper|less expensive|more expensive|'
        r'higher price|lower price|flexible|similar houses)\b',
        spoken, flags=re.I))
    # Unless explicitly requested, deviations are not sold as matches.
    optional=[compact(u) for u in alternatives[:3]] if alternatives_requested else []
    state['_allowed_alternative_units_this_turn']={
        u['unit_id'] for u in optional}
    return {
        'ok':True,
        'applied_criteria':{
            'budget':prefs['budget'],'bedrooms':prefs['bedrooms'],
            'preferred_property_type':chosen_type or 'Either',
            'property_filters':copy.deepcopy(refinements),
            'transaction_type':'sale',
        },
        'matching_properties':[compact(u) for u in primary],
        'optional_alternatives':optional,
        'out_of_budget_properties':[compact(u) for u in out_budget],
        'nearest_out_of_budget':compact(out_budget[0]) if out_budget else None,
        'match_status':'matches' if primary else 'no_matches',
        'other_nonmatching_properties':[compact(u) for u in other],
        'interpreted_purchase_deadline':deadline.isoformat() if deadline else None,
        'timeframe_interpretation':interpretation,
        'next_action':('Present only actual matching houses; identify any buyer-requested '
                       'alternative as OPTIONAL and explain the specific difference. '
                       'Do not bring up an alternative without a request; '
                       'do not push booking before answering the buyer.'),
        'note':'Buyer budget ceilings, comparative criteria and exact bedroom constraints are authoritative. '
               'Soft bedroom differences are labeled alternatives, NEVER matches. '
               'Inventory, prices and advisors are fictional.'
    }




def search_properties(state: dict, criteria: dict | None = None,
                      scope: str | None = None) -> dict:
    """Commercial search, isolated from a pending visit or main preferences.

    Hermes proposes typed criteria. The backend chooses whether a revision is
    supported by the buyer's wording; an exploration cannot silently persist.
    """
    utterance=str(state.get('_active_source_message_text') or '')
    base=(state.get('_turn_property_resolution') or {})
    source_unit=_units_by_code().get(base.get('unit_id')) if not base.get('ambiguous') else None
    try:
        search=search_sessions.derive(state,utterance,criteria,source_unit=source_unit)
    except (ValueError,TypeError) as exc:
        return {'error':str(exc),'error_code':'SEARCH_CRITERIA_INVALID',
                'next_action':'Use supported filters explicitly stated by the buyer.'}
    if scope and scope not in ('temporary','permanent'):
        return {'error':'Search scope must be temporary or permanent.',
                'error_code':'SEARCH_SCOPE_INVALID'}
    if scope=='permanent' and search['scope']!='permanent':
        # The model cannot promote exploratory options into permanent facts.
        search['scope']='temporary'
    fingerprint=json.dumps({
        'preferences':search['preferences'],'filters':search['filters'],
        'bedrooms_any':search['bedrooms_any']},sort_keys=True,default=str)
    cached=state.get('_search_result_cache_this_turn')
    if cached and cached.get('fingerprint')==fingerprint:
        return {**copy.deepcopy(cached['result']),'cached':True}
    result=evaluate_properties(
        state,_preferences_override=search['preferences'],
        _filters_override=search['filters'],_bedrooms_any=search['bedrooms_any'])
    if not result.get('ok'):
        return result
    session=search_sessions.save_search(state,search,result)
    state['conversation_phase']='exploration'
    state['stage']='Exploring property alternatives'
    # A tool execution does not rewrite the original buyer intent. An
    # explicit budget revision is both a preference update and a search.
    if state.get('_turn_intent')!='preference_update':
        state['_turn_intent']='property_search'
    state['_search_tool_called_this_turn']=True
    state['_matching_options_this_turn']=result['matching_properties']
    state['_search_out_of_budget_this_turn']=result.get('out_of_budget_properties') or []
    state['_search_nearest_out_of_budget_this_turn']=result.get('nearest_out_of_budget')
    record(state,'search_completed',f"{len(result['matching_properties'])} matches; {session['scope']} scope")
    response={**result,'scope':search['scope'],'active_search':{
        'revision':session['revision'],'source_message_id':session['source_message_id'],
        'criteria':session['criteria'],'scope':session['scope']},
        'primary_preferences_modified':False,
        'visit_draft_modified':False,
        'note':'This search does not change the appointment draft. The source of '
               'truth remains the server-verified catalogue and buyer constraints.'}
    state['_search_result_cache_this_turn']={
        'fingerprint':fingerprint,'result':copy.deepcopy(response)}
    return response



def compare_properties(state: dict, unit_ids: list[str] | None = None) -> dict:
    """Read-only grounded comparison; never selects a house or books a visit."""
    known=_units_by_code()
    spoken=str(state.get('_active_source_message_text') or '')
    named=conversation_context.mentioned_codes(spoken,known)
    requested=unit_ids or (named if len(named)>=2 else [])
    if not requested and re.search(
            r'\b(?:compare|difference|between|both|those two|these two)\b',
            spoken,re.I):
        requested=state.get('last_presented_units') or []
    requested=list(dict.fromkeys(requested))
    if not 2<=len(requested)<=3 or any(c not in known for c in requested):
        return {'error':'Specify two or three published houses to compare.',
                'error_code':'COMPARISON_NEEDS_HOUSES',
                'next_action':'Use two named houses or the last two offered options.'}
    if state.get('_active_source_message_id') and not (
            all(code in named for code in requested) or
            (set(requested).issubset(set(state.get('last_presented_units') or [])) and
             re.search(r'\b(?:compare|difference|between|both|those two|these two)\b',
                       spoken,re.I))):
        return {'error':'Comparison inputs must be grounded in this conversation.',
                'error_code':'COMPARISON_NOT_GROUNDED',
                'next_action':'Use houses named or recently offered to the buyer.'}
    entries=[]
    for code in requested:
        u=known[code]
        entries.append({
            'unit_id':code,'name':u['commercial_name'],
            'price_usd':u['price_usd'],'bedrooms':u['bedrooms'],
            'bathrooms':u['bathrooms'],'built_area_sqm':u['area_sqm'],
            'lot_area_sqm':u['lot_area_m2'],
            'confirmed_features':u.get('confirmed_features') or {},
            'availability':u['available_from_date'],
        })
    first=entries[0]
    differences={x['unit_id']:{
        'price_usd_vs_first':x['price_usd']-first['price_usd'],
        'built_area_sqm_vs_first':x['built_area_sqm']-first['built_area_sqm'],
        'bedrooms_vs_first':x['bedrooms']-first['bedrooms']
    } for x in entries[1:]}
    state['conversation_phase']='comparison'
    record(state,'comparison','Compared '+', '.join(requested))
    return {'ok':True,'properties':entries,'relative_to':first['unit_id'],
            'differences':differences,'selected_unit_id':None,
            'note':'Public comparison only. No unit selected for a visit.'}



def _buyer_mentioned_house(state: dict, unit_id: str) -> bool:
    """Require direct buyer reference, optionally by ordinal from the offered list."""
    spoken = str(state.get('_active_source_message_text') or '').lower()
    if str(unit_id).lower() in spoken:
        return True
    shortlist = state.get('shortlisted_unit_ids') or []
    import re
    for index, terms in ((0, r'\b(?:first(?: one| house| option)?|1st|option 1|#1)\b'),
                         (1, r'\b(?:second(?: one| house| option)?|2nd|option 2|#2)\b'),
                         (2, r'\b(?:third(?: one| house| option)?|3rd|option 3|#3)\b')):
        if index < len(shortlist) and shortlist[index] == unit_id and re.search(terms, spoken):
            return True
    return False


def unit_details(state: dict, unit_id: str | None = None) -> dict:
    """Expose verified details for ONE requested house, never all catalog units.

    The current buyer turn must name that house, or it must already be the
    conversational focus. Matching a search result alone is not buyer interest.
    """
    resolution=state.get('_turn_property_resolution') or {}
    if unit_id is None:
        unit_id=resolution.get('unit_id') or (
            _focus(state).get('unit_id') if not state.get('_active_source_message_id') else None)
    unit = next((u for u in read_catalog()['properties']
                 if u['unit_id'].casefold() == str(unit_id).strip().casefold()), None)
    if unit is None:
        return {'error':'No single published house can be identified.',
                'error_code':'REFERENCE_UNRESOLVED',
                'candidates':resolution.get('candidates',[]),
                'next_action':'Ask for a property only when the current options are ambiguous.'}
    resolution=state.get('_turn_property_resolution') or {}
    # Public factual reads require an unambiguous conversational reference,
    # never a buying-interest declaration or an appointment commitment.
    if state.get('_active_source_message_id'):
        if resolution.get('ambiguous'):
            return {'error':'More than one property could match this question.',
                    'error_code':'REFERENCE_AMBIGUOUS',
                    'candidates':resolution.get('candidates',[]),
                    'next_action':'Ask which one only if it is genuinely ambiguous.'}
        if resolution.get('unit_id')!=unit['unit_id']:
            return {'error':'The requested details do not match the discussed property.',
                    'error_code':'PROPERTY_NOT_IN_CONTEXT',
                    'resolved_unit_id':resolution.get('unit_id'),
                    'next_action':'Use resolved_unit_id if present; do not ask the buyer to reconfirm it.'}
    _set_focus(state, unit['unit_id'])
    state['conversation_phase']='property_details'
    return {'ok': True, 'unit_id': unit['unit_id'],
            'reference_resolution': state.get('_turn_property_resolution'),
            'commercial_name': unit['commercial_name'],
            'property_type': unit['property_type'],
            'bedrooms': unit['bedrooms'], 'bathrooms': unit['bathrooms'],
            'price_usd': unit['price_usd'], 'currency': unit.get('currency', 'USD'),
            'area_sqm': unit.get('area_sqm'), 'lot_area_m2': unit.get('lot_area_m2'),
            'confirmed_features': unit.get('confirmed_features', {}),
            'highlights': unit.get('highlights', []),
            'location': unit.get('location'),
            'status': unit['status'], 'available_from_date': unit['available_from_date'],
            'note': 'Verified information for this house only. Media require separate buyer request.'}


def unit_media(unit_id: str) -> dict:
    unit = next((u for u in read_catalog()['properties']
                 if u['unit_id'].casefold() == str(unit_id).strip().casefold()), None)
    if not unit:
        return {'error': f'Unknown residential unit: {unit_id}'}
    media = unit['media']
    gallery_path = media.get('gallery_url')
    # Never construct URLs from untrusted Host headers. Configure the public
    # domain for custom production domains or Preview environments.
    public_base = os.environ.get('DEMO_PUBLIC_BASE_URL', 'https://testing-hermes-agent-alpha.vercel.app').rstrip('/')
    if not (public_base.startswith('https://') or public_base.startswith('http://localhost:')):
        public_base = 'https://testing-hermes-agent-alpha.vercel.app'
    documents = [d for d in media.get('documents', [])
                 if d.get('scope') == 'unit' and d.get('unit_id') == unit['blackpenguin_unit_id']]
    return {
        'ok': True,
        'unit_id': unit['unit_id'],
        'gallery_url': f'{public_base}{gallery_path}' if gallery_path else None,
        'gallery_available': bool(gallery_path),
        'image_count': len(media.get('gallery_urls', [])),
        'documents': [{'type': d['type'], 'title': d['title'], 'url': f"{public_base}{d['url']}"}
                      for d in documents],
        'unit_pdf_available': bool(documents),
        'note': 'Only approved synthetic unit-specific PDFs are returned. Never substitute a project brochure.'
    }



def list_unit_assets(state: dict, unit_id: str | None = None) -> dict:
    """Read approved media availability without authorizing any URLs to send."""
    if not unit_id:
        unit_id=(state.get('_turn_property_resolution') or {}).get('unit_id')
    unit = _units_by_code().get(unit_id)
    if not unit:
        return {'error':'The referenced house is not available.',
                'error_code':'REFERENCE_UNRESOLVED',
                'next_action':'Ask which property only if no unique contextual reference exists.'}
    reference=state.get('_turn_property_resolution') or {}
    if state.get('_active_source_message_id') and (
            reference.get('ambiguous') or reference.get('unit_id')!=unit_id):
        return {'error':'The requested resources do not match an unambiguous property.',
                'error_code':'PROPERTY_NOT_IN_CONTEXT',
                'resolved_unit_id':reference.get('unit_id'),
                'next_action':'Resolve the current property from conversation context.'}
    media=unit_media(unit_id)
    if media.get('error'):
        return media
    previous=(state.get('shared_media') or {}).get(unit_id) or {}
    sent=set(previous.get('document_urls_sent') or [])
    return {'ok':True,'unit_id':unit_id,
            'gallery':{'available':media['gallery_available'],
                       'image_count':media['image_count'],
                       'already_shared':bool(previous.get('gallery_sent'))},
            'documents':[{'type':x['type'],'title':x['title'],
                          'already_shared':x['url'] in sent}
                         for x in media['documents']],
            'note':'Discovery returns NO URLs. Only deliver requested, approved assets; '
                   'a property fact sheet is not an architectural floor plan.'}



def date_input(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().strip('?.!,').lower()
    if text == 'today':
        return now_local().date().isoformat()
    if text == 'tomorrow':
        return (now_local().date() + timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    # Interpret a clear named date, including "November 27th".
    stripped = re.sub(r'(?<=\d)(?:st|nd|rd|th)\b', '', text)
    stripped = re.sub(r'\s+', ' ', stripped.replace(',', ' ')).strip()
    for fmt in ('%B %d %Y', '%b %d %Y', '%B %d', '%b %d'):
        try:
            parsed = datetime.strptime(stripped, fmt)
            candidate = date(parsed.year, parsed.month, parsed.day)
            if '%Y' not in fmt:
                year = now_local().year
                candidate = date(year, parsed.month, parsed.day)
                if candidate < now_local().date():
                    candidate = date(year + 1, parsed.month, parsed.day)
            return candidate.isoformat()
        except ValueError:
            continue
    raise ValueError('Provide a real date, e.g. November 27 or YYYY-MM-DD.')


def slots(state: dict, date_str: str | None = None, *,
          time_preference: str | None = None, extra_occupied: dict | None = None) -> dict:
    """Offer honest slot availability for one explicitly requested visit.

    Focus alone is not consent to book. The visit draft survives side questions.
    Broad date searches report days, not phantom offers or chosen hours.
    """
    known = _units_by_code()
    draft = _draft(state)
    unit_id = draft.get('unit_id')
    previous = state.get('booking_proposal') or {}
    if not unit_id and previous.get('selected_unit_id') in known:
        unit_id = previous['selected_unit_id']
        _set_visit_unit(state, unit_id)
    if unit_id not in known:
        focus = _focus(state)
        return _slot_error(
            'AMBIGUOUS_PROPERTY', 'Which house would you like to visit?',
            next_action=('Ask whether to visit ' + str(focus['unit_id']) +
                         ', the house in focus; do not ask for the property code again.'
                         if focus.get('unit_id') and not focus.get('ambiguous')
                         else 'Clarify which single house to visit.'))
    spoken = str(state.get('_active_source_message_text') or '').lower()
    if time_preference is None:
        words = [v for v in ('morning', 'afternoon', 'evening')
                 if re.search(r'\b' + v + r'\b', spoken)]
        if len(words) == 1:
            time_preference = words[0]
    if time_preference not in (None, '', 'any', 'morning', 'afternoon', 'evening'):
        return _slot_error('INVALID_TIME_PREFERENCE', 'Unsupported part of the day.',
                           unit_id, next_action='Use morning, afternoon, evening or any.')
    pol = scheduling.policy()
    today = scheduling.local_now().date()
    low = today + timedelta(days=1)
    high = today + timedelta(days=pol['booking_horizon_days'])
    relative = _relative_range(date_str, today) if date_str else None
    exact_date = None
    if date_str and not relative:
        try:
            exact_date = date.fromisoformat(_resolve_visit_date(state, date_str))
        except (ValueError, TypeError):
            return _slot_error('INVALID_DATE',
                'Please give a date, for example November 27, or say next week.',
                unit_id, next_action='Clarify only the date. Do not repeat the house question.')
        if not low <= exact_date <= high:
            return _slot_error('DATE_OUTSIDE_HORIZON',
                'Visits can be offered from tomorrow through ' + high.isoformat() + '.',
                unit_id, next_action='Choose a day within the available booking window.',
                allowed_start=low.isoformat(), allowed_end=high.isoformat())
    if relative:
        range_start, range_end = max(low, relative[0]), min(high, relative[1])
        if range_start > range_end:
            return _slot_error('DATE_OUTSIDE_HORIZON',
                'The requested period is outside the available booking horizon.',
                unit_id, next_action='Ask about a date within the published window.',
                allowed_start=low.isoformat(), allowed_end=high.isoformat())
    elif exact_date:
        range_start = range_end = exact_date
    else:
        range_start, range_end = low, high
    key = json.dumps([unit_id, date_str, time_preference], sort_keys=True)
    queries = state.setdefault('_slot_queries_this_turn', {})
    if key in queries:
        repeated = copy.deepcopy(queries[key])
        repeated['already_checked'] = True
        repeated['next_action'] = 'Do not call the same search again this turn. Answer using this result.'
        return repeated
    try:
        all_slots = scheduling.available_slots(
            state, date_filter=exact_date, extra_occupied=extra_occupied)
    except Exception:
        result = _slot_error('CALENDAR_UNAVAILABLE',
            'The appointment calendar could not be checked right now.', unit_id,
            next_action='Explain the temporary failure without inventing availability.')
        queries[key] = copy.deepcopy(result)
        return result
    def within(item: dict) -> bool:
        date_value = date.fromisoformat(item['date'])
        return range_start <= date_value <= range_end and _time_matches(item['start_time'], time_preference)
    options = scheduling.public_start_times([x for x in all_slots if within(x)])
    if exact_date is None:
        days = []
        for day_value in sorted({x['date'] for x in options})[:4]:
            day_options = [x for x in options if x['date'] == day_value]
            ranges = scheduling.time_ranges(day_options)
            if ranges:
                days.append({'date': day_value, 'first_start': ranges[0]['first_start'],
                             'last_start': ranges[-1]['last_start'], 'time_ranges': ranges})
        if not days:
            result = _slot_error('NO_SLOTS',
                'No visit dates match the requested period for ' + unit_id + '.',
                unit_id, next_action='Ask for another period or time of day; keep the house context.')
        else:
            result = {'ok': True, 'unit_id': unit_id, 'available_dates': days,
                      'proposal_id': None, 'timezone': pol['timezone'],
                      'next_action': 'Ask which date the buyer prefers for ' + unit_id +
                                     ', then search that exact day for hours.'}
        state['last_slot_search'] = {'unit_id': unit_id, 'requested': date_str,
                                    'time_preference': time_preference,
                                    'result_code': 'OK' if result.get('ok') else 'NO_SLOTS'}
        queries[key] = copy.deepcopy(result)
        return result
    if not options:
        # A failed exploratory search must not delete a previously selected hour.
        # The buyer may switch back to the last valid proposal while it lives.
        same_day = scheduling.public_start_times([
            item for item in all_slots if item['date'] == exact_date.isoformat()])
        result = _slot_error('NO_SLOTS',
            f'No {time_preference or "available"} visit times for {unit_id} on {exact_date.isoformat()}.',
            unit_id,
            next_action='Offer another available time on this day or ask for another day; do not re-ask the house.',
            date=exact_date.isoformat(),
            other_time_ranges=scheduling.time_ranges(same_day)[:5])
        state['last_slot_search'] = {'unit_id': unit_id, 'requested': date_str,
                                    'time_preference': time_preference, 'result_code': 'NO_SLOTS'}
        queries[key] = copy.deepcopy(result)
        return result
    state['last_offered_date'] = exact_date.isoformat()
    # Look up previously offered choices without assuming the most recent
    # query is the buyer's selected appointment.
    candidates=list((state.get('booking_offers') or {}).values())
    if previous and previous.get('proposal_id') not in {p.get('proposal_id') for p in candidates}:
        candidates.append(previous)
    previous=next((p for p in reversed(candidates) if
        p.get('selected_unit_id')==unit_id and
        p.get('date')==exact_date.isoformat() and
        p.get('time_preference')==time_preference and
        p.get('status')=='offered'), {})
    if (previous.get('status') == 'offered' and previous.get('selected_unit_id') == unit_id
            and previous.get('date') == exact_date.isoformat()
            and previous.get('time_preference') == time_preference):
        try:
            if datetime.now(timezone.utc) < datetime.fromisoformat(
                    previous['expires_at'].replace('Z', '+00:00')):
                result = {'ok': True, 'unit_id': unit_id, 'date': exact_date.isoformat(),
                          'available_slots': previous['options'],
                          'time_ranges': scheduling.time_ranges(previous['options']),
                          'proposal_id': previous['proposal_id'],
                          'expires_at': previous['expires_at'], 'timezone': pol['timezone'],
                          'notice': 'Existing proposal; slots are not reserved.'}
                state['last_slot_search']={'unit_id':unit_id,'requested':date_str,
                    'date':exact_date.isoformat(),'time_preference':time_preference,
                    'result_code':'OK'}
                queries[key] = copy.deepcopy(result)
                return result
        except (KeyError, ValueError, TypeError):
            pass
    proposal_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) +
                  timedelta(minutes=pol['offer_expiration_minutes'])).isoformat()
    proposal = {'version': 'hermes-demo/booking-proposal/2.0',
                'proposal_id': proposal_id, 'status': 'offered',
                'selected_unit_id': unit_id, 'date': exact_date.isoformat(),
                'time_preference': time_preference, 'options': options,
                'offer_source_message_id': state.get('_active_source_message_id'),
                'expires_at': expires_at, 'timezone': pol['timezone']}
    offers=state.setdefault('booking_offers',{})
    offers[proposal_id]=proposal
    # Bound memory growth. No booked appointment is ever deleted by this cache.
    if len(offers)>20:
        for old_id in list(offers)[:-20]:
            if old_id!=draft.get('proposal_id'):
                offers.pop(old_id,None)
    selected=(draft.get('status')=='awaiting_confirmation' and
              draft.get('unit_id')==unit_id and draft.get('proposal_id') and
              draft.get('start_time'))
    if not selected:
        state['booking_proposal']=proposal
        draft.update(unit_id=unit_id,date=exact_date.isoformat(),
                     start_time=None,proposal_id=None,status='hour_needed')
    dialogue = state['bp_episode']['sales_dialogue_states']['state_json']
    dialogue['booking_search'] = {'requested_date': exact_date.isoformat(),
                                  'timezone': pol['timezone'],
                                  'unit_id': unit_id, 'time_preference': time_preference}
    dialogue['booking_proposal'] = copy.deepcopy(state.get('booking_proposal'))
    dialogue['visit_draft'] = copy.deepcopy(draft)
    state['bp_episode']['sales_dialogue_states']['revision'] += 1
    state['last_slot_search'] = {'unit_id': unit_id, 'requested': date_str,
                                'date':exact_date.isoformat(),
                                'time_preference': time_preference, 'result_code': 'OK'}
    record(state, 'slot_offer',
           f'{len(options)} hourly start times offered for {unit_id} on {exact_date.isoformat()}')
    result = {'ok': True, 'unit_id': unit_id, 'date': exact_date.isoformat(),
              'available_slots': options, 'time_ranges': scheduling.time_ranges(options),
              'proposal_id': proposal_id, 'expires_at': expires_at,
              'timezone': pol['timezone'],
              'visit_duration_minutes': pol['visit_duration_minutes'],
              'buffer_minutes': pol['buffer_after_minutes'],
              'notice': 'No advisor assigned and no option is held. Buyer must select an hour and confirm later.'}
    queries[key] = copy.deepcopy(result)
    return result


def redact_unconfirmed_advisor_names(reply: str, state: dict) -> str:
    """Prevent leaking known human advisor names before a persisted visit.

    The model may still hallucinate unknown names; this is a narrow protection
    for the actual synthetic team and is not a replacement for conversation
    policy or end-to-end evaluation.
    """
    from . import scheduling
    confirmed = {b.get('advisor_name') for b in state.get('booked_visits', [])
                 if b.get('status') == 'confirmed'}
    cleaned = reply
    for user in scheduling.eligible_users(scheduling.policy()['project_id']):
        if user['display_name'] in confirmed:
            continue
        cleaned = re.sub(re.escape(user['display_name']), 'the assigned sales advisor',
                         cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b'+re.escape(user['demo_advisor_id'])+r'\b',
                         'an assigned sales advisor', cleaned, flags=re.IGNORECASE)
    return cleaned


def select_visit_time(state: dict, proposal_id: str, date_str: str, start_time: str) -> dict:
    """Record one exact hour chosen by the LEAD, with message provenance.

    A model cannot silently pick among offered hours. The lead's current
    message must contain the clock time, and booking requires later consent.
    """
    proposal=(state.get('booking_offers') or {}).get(proposal_id) or state.get('booking_proposal')
    if not proposal or proposal.get('status') != 'offered' or proposal.get('proposal_id') != proposal_id:
        return {'error': 'No matching active offer. Search availability again.'}
    try:
        parsed_time = time.fromisoformat(start_time).strftime('%H:%M')
        parsed_day = date_input(date_str)
        if datetime.now(timezone.utc) >= datetime.fromisoformat(proposal['expires_at'].replace('Z', '+00:00')):
            return {'error': 'The offer expired. Search for new availability.'}
    except (ValueError, TypeError):
        return {'error': 'Invalid visit date or time.'}
    if proposal.get('selected_unit_id')!=_draft(state).get('unit_id'):
        return {'error':'This offer belongs to a different property from the current visit draft.'}
    if not any(slot['date'] == parsed_day and slot['start_time'] == parsed_time
               for slot in proposal['options']):
        return {'error': 'This hour was not offered.'}
    spoken = str(state.get('_active_source_message_text') or '').lower()
    # An hour without an explicit date refers to the LAST exact date offered
    # to the lead, not an older or merely searched proposal.
    has_date = (parsed_day in spoken or
        bool(re.search(r'\b(?:' + str(int(parsed_day[-2:])) +
            r')(?:st|nd|rd|th)?\s+(?:nov|dec|jan|feb|mar|apr|may|jun|jul|aug|sep|oct)',spoken)) or
        bool(re.search(r'\b(?:nov|dec|jan|feb|mar|apr|may|jun|jul|aug|sep|oct)\w*\s+' +
            str(int(parsed_day[-2:])) + r'(?:st|nd|rd|th)?\b',spoken)))
    if not has_date and state.get('last_offered_date') not in (None,parsed_day):
        return {'error':'Selected hour must belong to the most recently offered date.'}
    hour = int(parsed_time[:2]); twelve = hour % 12 or 12
    meridian = 'am' if hour < 12 else 'pm'
    possible = (rf'\b0?{hour}:00\b',
                rf'\b{twelve}(?::00)?\s*(?:{meridian}|{meridian[0]}\.m\.)\b')
    if not spoken or not any(re.search(pattern, spoken) for pattern in possible):
        return {'error': 'Ask the lead to explicitly choose a clock time, such as 9 AM.'}
    proposal.update(selected_start_time=parsed_time, selected_date=parsed_day,
                    selected_source_message_id=state.get('_active_source_message_id'))
    state.setdefault('booking_offers',{})[proposal_id]=proposal
    state['booking_proposal']=proposal
    draft=_draft(state)
    draft.update(unit_id=proposal['selected_unit_id'], date=parsed_day,
                 start_time=parsed_time,proposal_id=proposal_id,status='awaiting_confirmation')
    state['confirmation_request']=None
    state['bp_episode']['sales_dialogue_states']['state_json']['booking_proposal'] = copy.deepcopy(proposal)
    state['bp_episode']['sales_dialogue_states']['state_json']['visit_draft'] = copy.deepcopy(draft)
    record(state, 'hour_selected', f'Lead chose {parsed_day} {parsed_time} for {proposal["selected_unit_id"]}')
    return {'ok': True, 'date': parsed_day, 'start_time': parsed_time,
            'unit_id': proposal['selected_unit_id'],
            'notice': 'Hour chosen but NOT booked. Ask for explicit confirmation in a later lead message.'}


def track_property_interest(state: dict, unit_id: str, status: str = 'discussed') -> dict:
    """Track a named unit with message provenance, never infer buying intent."""
    known = {u['unit_id'] for u in read_catalog()['properties']}
    if unit_id not in known:
        return {'error': 'Unknown or non-public residential unit.'}
    if status not in ('discussed', 'considering', 'interested'):
        return {'error': 'Invalid interest status.'}
    reference=state.get('_turn_property_resolution') or {}
    if state.get('_active_source_message_id') and (
            reference.get('ambiguous') or reference.get('unit_id')!=unit_id):
        return {'error':'Cannot record interest without the correctly resolved house.',
                'error_code':'PROPERTY_NOT_IN_CONTEXT',
                'resolved_unit_id':reference.get('unit_id'),
                'next_action':'Use resolved_unit_id; do not ask the buyer to repeat the house code.'}
    if status == 'interested' and state.get('_active_source_message_id'):
        words = str(state.get('_active_source_message_text') or '').lower()
        # Merely asking for information or photos is NOT visit intent.
        visit_intent = re.search(r'\b(visit|tour|book|schedule|see in person|appointment|choose|select|pick|want|interested|like|prefer)\b', words)
        if not visit_intent and _draft(state).get('unit_id') != unit_id and state['lead'].get('interest_unit_id') != unit_id:
            return {'error': 'Asking about a house is not permission to select it for a visit.'}
    existing = state.setdefault('property_interests', [])
    # A mention cannot downgrade a prior explicit interest.
    rank = {'discussed': 0, 'considering': 1, 'interested': 2}
    previous = next((x for x in existing if x['unit_id'] == unit_id), None)
    if previous is not None:
        if rank[status] >= rank[previous['status']]:
            previous.update(status=status, source_message_id=state.get('_active_source_message_id'))
    else:
        existing.append({'unit_id': unit_id, 'status': status,
                         'source_message_id': state.get('_active_source_message_id')})
    if status == 'interested':
        state['lead']['interest_unit_id'] = unit_id
    if _buyer_mentioned_house(state, unit_id) or status == 'interested':
        _set_focus(state, unit_id)
    if status == 'interested' and re.search(
            r'\b(?:visit|tour|book|schedule|appointment|see in person)\b',
            str(state.get('_active_source_message_text') or ''), flags=re.I):
        _set_visit_unit(state, unit_id)
    return {'ok': True, 'property_interests': existing,
            'active_unit_id': state['lead'].get('interest_unit_id')}


def update_lead(state: dict, **kwargs: Any) -> dict:
    """Update the 9 UI fields and any of BP's 16 declared commercial facts.

    A declared fact is never treated as a verified bank balance, credit result,
    identity or consent. Unknown fields are rejected, not silently discarded.
    """
    lead = state['lead']
    if state.get('_turn_scope')=='temporary':
        # Tool writes cannot turn a comparison into a permanent qualification.
        blocked={'budget_usd','min_bedrooms','budget','budget_minimum',
                 'budget_maximum','bedrooms','property_type','purchase_timeframe'}
        requested=set(kwargs)
        if requested & blocked:
            rest={k:v for k,v in kwargs.items() if k not in blocked}
            if not rest:
                return {'ok':True,'changed_fields':{},
                        'scope':'temporary',
                        'note':'Exploratory criteria belong in search_properties.'}
            kwargs=rest
    supported = set(lead)
    changed = {}
    bp_changed = {}
    valid_types = {'Apartment', 'House', 'Either'}
    for key, value in kwargs.items():
        if key not in supported and key not in BP_COMMERCIAL_FIELDS:
            return {'error': f'Unknown lead-profile field: {key}'}
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            if len(value) > 200:
                return {'error': f'{key} is too long'}
        if key in ('budget_usd','min_bedrooms','budget_minimum','budget_maximum','bedrooms'):
            try:
                value = int(value)
                max_value = 20 if key in ('min_bedrooms','bedrooms') else 100_000_000
                if not 0 < value < max_value:
                    raise ValueError
            except (TypeError,ValueError):
                return {'error': f'{key} must be a positive integer'}
        if key == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', str(value)):
            return {'error': 'Invalid email format'}
        if key == 'phone' and not re.fullmatch(r'\+?[\d\s()\-]{7,22}', str(value)):
            return {'error': 'Invalid phone format'}
        if key == 'property_type':
            canon = next((x for x in valid_types if x.casefold()==str(value).casefold()),None)
            if canon is None:
                return {'error':'property_type must be Apartment, House or Either'}
            value=canon
        if key == 'interest_unit_id':
            choices={u['unit_id'] for u in read_catalog()['properties']}
            if value not in choices:
                return {'error': f'Unknown available unit: {value}'}
            if state.get('_active_source_message_id') and value != lead.get('interest_unit_id'):
                spoken = str(state.get('_active_source_message_text') or '').lower()
                contextual = (_focus(state).get('unit_id') == value and
                              not _focus(state).get('ambiguous') and
                              not any(code.lower() in spoken for code in _units_by_code() if code != value))
                if not (_buyer_mentioned_house(state, str(value)) or contextual) or not re.search(
                        r'\b(visit|tour|book|schedule|see in person|appointment|choose|select|pick|want|interested|like|prefer)\b', spoken):
                    return {'error': 'A question about a house is not explicit visit selection.'}
        if key == 'budget_currency':
            value=str(value).upper()
            if not re.fullmatch(r'[A-Z]{3}',value):
                return {'error':'budget_currency must be a 3-letter currency code'}
        if key in supported:
            if lead[key]!=value:
                changed[key]=value
        else:
            previous=state['bp_episode']['leads']['lead_profile_data']['sales_profile']['current'].get(key)
            if not previous or previous['value']!=value:
                bp_changed[key]=value
    # When a canonical field is explicitly collected, update the UI projection.
    aliases={'budget_maximum':'budget_usd','purchase_timeline':'purchase_timeframe',
             'bedrooms':'min_bedrooms'}
    for canonical, ui_field in aliases.items():
        if canonical in bp_changed and ui_field not in changed and lead.get(ui_field)!=bp_changed[canonical]:
            changed[ui_field]=bp_changed[canonical]
    interest=bp_changed.get('property_interest')
    if isinstance(interest,str) and interest in {u['unit_id'] for u in read_catalog()['properties']}:
        if 'interest_unit_id' not in changed and lead.get('interest_unit_id')!=interest:
            changed['interest_unit_id']=interest
    # A model replay of a parsed buyer declaration must not reintroduce a
    # stale spending ceiling or turn an exact/preferred bedroom count into MIN.
    structured=_preferences(state)
    if structured.get('last_source_message_id')==state.get('_active_source_message_id'):
        declared=sales_preferences.parse_declarations(state.get('_active_source_message_text') or '')
        if 'budget' in declared:
            for field in ('budget_usd',):
                changed.pop(field,None)
            for field in ('budget_minimum','budget_maximum','budget'):
                bp_changed.pop(field,None)
        if 'bedrooms' in declared:
            changed.pop('min_bedrooms',None)
            bp_changed.pop('bedrooms',None)
    if changed or bp_changed:
        lead.update(changed)
        # UI-only fields (e.g. House vs Apartment) are not part of BP's 16
        # canonical commercial facts. Keep their provenance in the episode too.
        active_message = state.get('_active_source_message_id')
        if active_message:
            for field in changed:
                state.setdefault('ui_fact_evidence', {})[field] = {
                    'source_message_id': active_message,
                    'evidence': state.get('_active_source_message_text') or '',
                    'captured_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                    'source': 'active_conversation',
                }
        # Give explicitly supplied BP-native values priority over legacy alias projections.
        merged=dict(changed)
        canonical_alias={'budget_usd':'budget','purchase_timeframe':'purchase_timeline',
                         'min_bedrooms':'bedrooms','interest_unit_id':'property_interest'}
        for field, value in bp_changed.items():
            for alias, canonical in list(canonical_alias.items()):
                if canonical==field and alias in merged:
                    del merged[alias]
            merged[field]=value
        _capture_bp_facts(state, merged)
        # Normalize legacy Hermes profile writes that were not already parsed
        # from a direct declaration; otherwise only the UI mirror would change.
        current_source=state.get('_active_source_message_id')
        if _preferences(state).get('last_source_message_id')!=current_source:
            budgets={}
            if 'budget_maximum' in bp_changed:
                budgets={'mode':'cap','maximum_usd':bp_changed['budget_maximum']}
            elif 'budget_usd' in changed:
                budgets={'mode':'cap','maximum_usd':changed['budget_usd']}
            if 'budget_minimum' in bp_changed:
                budgets['minimum_usd']=bp_changed['budget_minimum']
                budgets['mode']='range' if 'maximum_usd' in budgets else 'floor'
            beds={}
            if 'min_bedrooms' in changed or 'bedrooms' in bp_changed:
                beds={'mode':'minimum','minimum':bp_changed.get('bedrooms',changed.get('min_bedrooms'))}
            if budgets or beds:
                sales_preferences.apply(state,
                    {'budget':budgets,'bedrooms':beds}, source_message_id=current_source)
        if changed.get('interest_unit_id'):
            _set_focus(state, changed['interest_unit_id'])
        if lead.get('budget_usd') and lead.get('purchase_timeframe'):
            state['stage']='Qualified lead'
        if lead.get('interest_unit_id'):
            state['stage']='Property selected'
        record(state,'lead_update','Updated '+', '.join(k.replace('_',' ') for k in sorted(set(changed)|set(bp_changed))))
    return {'ok':True,'changed_fields':changed,'changed_profile_facts':bp_changed,
            'lead_profile':lead,'stage':state['stage']}


def explicitly_declared_preferences(message: str) -> dict:
    """Conservative deterministic guard for *unambiguous* direct requests.

    Hermes remains responsible for commercial reasoning and calls all regular
    domain tools. This deliberately tiny validator makes a clearly stated
    "2-bedroom house" observable if the model omits update_lead_profile.
    We never infer a budget, a particular unit, financing or buying intent.
    """
    text = str(message or '').strip()
    if not text or '?' in text:
        return {}
    intent = re.search(r"\b(?:i(?:'m| am)\s+looking\s+for|i\s+(?:need|want|prefer)|"
                       r"we(?:'re| are)\s+looking\s+for|i(?:'d| would)\s+like)\b"
                       r"(?P<request>.{1,130})", text, flags=re.I)
    if not intent:
        return {}
    request = intent.group('request').split(';')[0]
    # Uncertainty, negation and alternatives are not safe to assign.
    if re.search(r"\b(?:not|don't|dont|rather than|instead of|either|or|maybe|perhaps|possibly)\b", request, re.I):
        return {}
    kinds = set(x.lower() for x in re.findall(r'\b(?:house|apartment)\b', request, re.I))
    if len(kinds) != 1:
        return {}
    result = {'property_type': 'House' if 'house' in kinds else 'Apartment'}
    beds = re.search(r'\b([1-9]|1[0-9])\s*[- ]?\s*(?:bedroom|bedrooms|bed|beds)\b', request, re.I)
    if not beds:
        beds = re.search(r'\b(?:bedrooms|beds)\s*(?:of|:)?\s*([1-9]|1[0-9])\b', request, re.I)
    if beds:
        result['min_bedrooms'] = int(beds.group(1))
    return result


def verify_explicit_preferences(state: dict, message: str) -> dict:
    """Validate exact declarations through the *existing* domain tool path.

    No second LLM, no speculative profile facts. An audit event distinguishes
    this protective validation from Hermes-originated tool calls.
    """
    if (state.get('_turn_intent')=='property_search' and
            state.get('_turn_scope')=='temporary'):
        return {'ok':True,'scope':'temporary','changed_fields':{},
                'note':'Exploratory request. Persistent buyer preferences preserved.'}
    values = explicitly_declared_preferences(message)
    missing = {key: val for key, val in values.items() if state['lead'].get(key) != val}
    if missing:
        response = update_lead(state, **missing)
        if response.get('error'):
            return response
        state.setdefault('tool_observations', []).append({
            'tool':'explicit_preference_validator','status':'applied',
            'fields':list(missing),'message_id':state.get('_active_source_message_id'),
            'timestamp':now_local().isoformat(),
        })
    else:
        response={'ok':True,'changed_fields':{}}
    explicit=sales_preferences.parse_declarations(message)
    if explicit:
        typed={}
        for name,values in explicit.items():
            for k,v in values.items():
                alias={'minimum_usd':'budget_minimum_usd','maximum_usd':'budget_maximum_usd',
                       'target_usd':'budget_target_usd','exact_price_usd':'budget_exact_price_usd',
                       'mode':f'{name}_mode','minimum':'bedrooms_min','maximum':'bedrooms_max',
                       'preferred':'bedrooms_preferred'}.get(k)
                if alias:typed[alias]=v
        result=update_search_preferences(state,**typed)
        if result.get('error'):return result
        if result.get('changed_fields'):
            state.setdefault('tool_observations',[]).append({
                'tool':'explicit_preference_validator','status':'applied',
                'fields':result['changed_fields'],
                'message_id':state.get('_active_source_message_id'),
                'timestamp':now_local().isoformat()})
    # Incremental filters can be collected in any order, or together in one
    # message, without forcing the buyer to restart discovery.
    ref=state.get('_turn_property_resolution') or {}
    base=_units_by_code().get(ref.get('unit_id')) if not ref.get('ambiguous') else None
    refinements=property_filters.parse(message,base)
    bedroom_min=refinements.pop('_bedrooms_minimum',None)
    bedroom_max=refinements.pop('_bedrooms_maximum',None)
    if bedroom_min is not None or bedroom_max is not None:
        b={'mode':'minimum','minimum':bedroom_min} if bedroom_min is not None else {
            'mode':'maximum','maximum':bedroom_max}
        result=sales_preferences.apply(state,{'bedrooms':b},
            source_message_id=state.get('_active_source_message_id'))
        if result.get('error'):
            return result
        if result.get('changed_fields'):
            state.setdefault('tool_observations',[]).append({
                'tool':'relative_bedroom_refinement','status':'applied',
                'fields':result['changed_fields'],
                'message_id':state.get('_active_source_message_id'),
                'timestamp':now_local().isoformat()})
    if refinements:
        change=property_filters.update(
            state,refinements,state.get('_active_source_message_id'))
        if change.get('error'):
            return change
        if change.get('changed_fields'):
            state['conversation_phase']='search'
            state.setdefault('tool_observations',[]).append({
                'tool':'property_filter_refinement','status':'applied',
                'fields':change['changed_fields'],
                'message_id':state.get('_active_source_message_id'),
                'timestamp':now_local().isoformat()})
            record(state,'property_filters_updated',
                   'Refined '+', '.join(change['changed_fields']))
    state['tool_observations']=state.get('tool_observations',[])[-50:]
    return response


def _explicit_lead_confirmation(state: dict) -> bool:
    """Accept a clearly affirmative answer to the exact outstanding recap.

    Bare "OK", "sounds good" or a statement about other topics is NOT booking
    consent. The buyer must have seen the complete property/date/time recap.
    """
    text=str(state.get('_active_source_message_text') or '').strip().lower()
    if not text or re.search(r'\b(?:not|don.t|never|cancel|no|without)\b',text):
        return False
    request=state.get('confirmation_request') or {}
    draft=_draft(state)
    if not request or any(request.get(k)!=draft.get(k) for k in ('unit_id','date','start_time')):
        return False
    if request.get('prompt_message_id') == state.get('_active_source_message_id'):
        return False
    return bool(re.fullmatch(
        r'\s*(?:yes[\s,!.]*(?:i\s+)?(?:confirm|accept|book\s+it|please\s+book\s+it)|'
        r'i\s+confirm|i\s+accept|confirm(?:\s+the\s+visit)?|'
        r'please\s+(?:book|confirm|reserve)\s+(?:it|the\s+visit)|'
        r'si[\s,!.]*(?:confirmo|acepto)|sí[\s,!.]*(?:confirmo|acepto)|'
        r'confirmo|acepto|understood[;,.!\s]+i\s+confirm)[.!]?\s*',
        text, flags=re.I))


def capture_explicit_visit_time(state: dict, message: str) -> dict:
    """Record an unambiguous hour from the latest offer, even if Hermes misses the tool."""
    text=str(message or '').lower()
    if re.search(r'\b(?:not|no|instead|cancel|maybe|or)\b',text):
        return {'ok':True,'captured':False}
    times=[]
    for m in re.finditer(r'\b(\d{1,2})(?::(00))?\s*(am|pm)\b|\b([01]\d|2[0-3]):00\b',text,re.I):
        if m.group(4):
            times.append(m.group(4)+':00')
        else:
            hour=int(m.group(1))%12+(12 if m.group(3)=='pm' else 0)
            times.append(f'{hour:02d}:00')
    if len(set(times))!=1:
        return {'ok':True,'captured':False}
    day=state.get('last_offered_date')
    # An explicitly specified YYYY-MM-DD may override the last offered day.
    specified=re.search(r'\b20\d{2}-\d{2}-\d{2}\b',text)
    if specified:day=specified.group(0)
    if not day:
        return {'ok':True,'captured':False}
    offers=[o for o in (state.get('booking_offers') or {}).values()
            if o.get('date')==day and o.get('selected_unit_id')==_draft(state).get('unit_id') and
            o.get('status')=='offered' and
            any(x['start_time']==times[0] for x in o.get('options',[]))]
    if not offers:
        candidate=state.get('booking_proposal')
        if candidate and candidate.get('date')==day and candidate.get('status')=='offered':
            offers=[candidate]
    if not offers:
        return {'ok':True,'captured':False}
    selected=select_visit_time(state,offers[-1]['proposal_id'],day,times[0])
    if selected.get('ok'):
        record(state,'visit_time_captured',f'{day} {times[0]} from an explicit buyer message')
        return {'ok':True,'captured':True,'selection':selected}
    return {'ok':True,'captured':False,'reason':selected.get('error')}


def ready_to_finalize_visit(state: dict) -> bool:
    draft=_draft(state)
    return bool(draft.get('status')=='awaiting_confirmation'
        and draft.get('proposal_id')
        and _explicit_lead_confirmation(state)
        and (state.get('booking_proposal') or {}).get('proposal_id')==draft.get('proposal_id'))


def book_visit(state: dict, first_name: str, last_name: str, email: str,
               phone: str, advisor_id: str | None = None, date_str: str = '',
               start_time: str = '', proposal_id: str = '', unit_id: str | None = None,
               replaces_meeting_id: str | None = None, extra_occupied: dict | None = None) -> dict:
    """Validate explicit consent and prepare a booking without naming an advisor.

    The shared Turso transaction chooses and reserves the advisor, atomically.
    No pre-commit booking status or advisor name is exposed to Hermes.
    """
    pol = scheduling.policy()
    active = [b for b in state['booked_visits'] if b.get('status') == 'confirmed']
    if active and (len(active) != 1 or active[0]['meeting_id'] != replaces_meeting_id):
        return {'error': 'A confirmed visit already exists. Reschedule or cancel it first.'}
    if replaces_meeting_id and not active:
        return {'error': 'No active visit exists to replace.'}
    proposal = state.get('booking_proposal')
    if not proposal or proposal.get('status') != 'offered' or proposal.get('proposal_id') != proposal_id:
        return {'error': 'There is no matching active appointment offer. Search slots again.'}
    try:
        if datetime.now(timezone.utc) >= datetime.fromisoformat(proposal['expires_at'].replace('Z', '+00:00')):
            return {'error': 'Appointment offer expired. Search availability again.'}
        day = date_input(date_str)
        start_time = time.fromisoformat(start_time).strftime('%H:%M')
        if not day or int(start_time[3:]) != 0:
            return {'error': 'Visits must start on a full hour.'}
    except (ValueError, TypeError):
        return {'error': 'Invalid booking date or time.'}
    if not _explicit_lead_confirmation(state):
        return {'error': 'Explicit acceptance from the lead is required in a subsequent message.'}
    if proposal.get('offer_source_message_id') == state.get('_active_source_message_id'):
        return {'error': 'The lead must confirm in a later turn.'}
    if not any(opt['date'] == day and opt['start_time'] == start_time for opt in proposal['options']):
        return {'error': 'The selected hour was not offered. Search again.'}
    if (proposal.get('selected_start_time') != start_time or proposal.get('selected_date') != day or
            not proposal.get('selected_source_message_id') or
            proposal.get('selected_source_message_id') == state.get('_active_source_message_id')):
        return {'error': 'The lead must explicitly choose the offered hour, then confirm in a later turn.'}
    units = {u['unit_id']: u for u in read_catalog()['properties']}
    chosen = unit_id or _draft(state).get('unit_id') or state['lead'].get('interest_unit_id')
    if not chosen or chosen not in units or chosen != proposal['selected_unit_id']:
        return {'error': 'Explicitly select one available house before confirming its visit.'}
    if _draft(state).get('unit_id') != chosen:
        return {'error': 'The visit draft does not identify this house. Confirm the chosen house first.'}
    if (_draft(state).get('date') != day or _draft(state).get('start_time') != start_time):
        return {'error': 'The chosen date and hour must match the visit awaiting confirmation.'}
    if not all(isinstance(x, str) and x.strip() for x in (first_name, last_name, email, phone)):
        return {'error': 'First name, last name, email and phone are required.'}
    choices = scheduling.available_slots(state, date_filter=date.fromisoformat(day), extra_occupied=extra_occupied)
    if not any(opt['date'] == day and opt['start_time'] == start_time for opt in choices):
        return {'error': 'That hour is no longer available. Search again.'}
    changed = update_lead(state, first_name=first_name, last_name=last_name, email=email, phone=phone)
    if 'error' in changed:
        return changed
    selected = units[chosen]
    starts_at = scheduling.instant(date.fromisoformat(day), start_time)
    visit_end = starts_at + timedelta(minutes=pol['visit_duration_minutes'])
    buffered_end = visit_end + timedelta(minutes=pol['buffer_after_minutes'])
    meeting_id = str(uuid.uuid4()); operation_id = str(uuid.uuid4())
    bp = state['bp_episode']; project = read_catalog()['project_info']
    # Synthetic location must not produce a real Google Maps pin.
    property_location = selected.get('location') or {}
    visit_location = {'label': 'Synthetic project showroom',
                      'address': project['location']['street_address'],
                      'verification_status': 'synthetic', 'maps_url': None,
                      'note': 'Fictional address; no actual physical appointment is offered.'}
    booking = {'booking_id': 'DEMO-' + meeting_id[:8].upper(), 'meeting_id': meeting_id,
               'proposal_id': proposal_id, 'first_name': first_name.strip(),
               'last_name': last_name.strip(), 'email': email.strip(), 'phone': phone.strip(),
               'unit_id': chosen, 'advisor_id': None, 'advisor_name': None,
               'date': day, 'start_time': start_time,
               'end_time': visit_end.astimezone(PROJECT_TZ).strftime('%H:%M'),
               'buffer_until': buffered_end.astimezone(PROJECT_TZ).strftime('%H:%M'),
               'timezone': pol['timezone'], 'location': visit_location['label'],
               'visit_location': visit_location, 'visit_duration_minutes': pol['visit_duration_minutes'],
               'buffer_minutes': pol['buffer_after_minutes'],
               'simulation_only': True, 'status': 'pending_assignment'}
    context = {'version': 'sales-visit-context/2.0', 'source': 'explicit_booking_selection',
               'project_id': bp['ids']['project_id'], 'project_name': project['name'],
               'property_type_id': selected['property_type_id'],
               'property_type_name': selected['commercial_name'],
               'unit_id': selected['blackpenguin_unit_id'], 'unit_code': selected['unit_id'],
               'timezone': pol['timezone'], 'property_association': 'explicit',
               'visit_location': visit_location,
               'property_location': copy.deepcopy(property_location)}
    meeting = {'id': meeting_id, 'project_id': bp['ids']['project_id'],
               'lead_id': bp['ids']['lead_id'], 'broker_id': None,
               'assigned_sales_user_id': None,
               'meeting_time': starts_at.isoformat().replace('+00:00', 'Z'),
               'meeting_timezone': pol['timezone'], 'duration_minutes': pol['visit_duration_minutes'],
               'buffer_after_minutes': pol['buffer_after_minutes'], 'modality': 'showroom',
               'confirmation_status': 'pending_assignment', 'calendar_sync_status': 'simulation_ready',
               'visit_location_label': visit_location['label'], 'visit_address': visit_location['address'],
               'visit_property_type_id': context['property_type_id'],
               'visit_unit_id': context['unit_id'], 'visit_context': context,
               'location_confirmation_status': 'synthetic',
               'status': 'pending_assignment', 'source': 'agent_simulation',
               'is_demo': True, 'gcal_event_id': None,
               'created_at': datetime.now(timezone.utc).isoformat()}
    operation = {'id': operation_id, 'company_id': bp['ids']['company_id'],
                 'project_id': bp['ids']['project_id'],
                 'conversation_id': bp['ids']['conversation_id'], 'lead_id': bp['ids']['lead_id'],
                 'proposal_id': proposal_id, 'meeting_id': meeting_id,
                 'snapshot_json': {'version': 'hermes-demo/booking-receipt/2.0',
                                   'context': context, 'option': {'starts_at': meeting['meeting_time'],
                                                                 'sales_user_id': None},
                                   'preferences': {'duration_minutes': pol['visit_duration_minutes'],
                                                   'buffer_after_minutes': pol['buffer_after_minutes']}},
                 'calendar_status': 'simulation', 'calendar_attempts': 0,
                 'next_attempt_at': None, 'created_at': datetime.now(timezone.utc).isoformat()}
    bp['meetings'].append(meeting)
    bp['sales_booking_operations'].append(operation)
    state['booked_visits'].append(booking)
    proposal['status'] = 'pending_assignment'; proposal['meeting_id'] = meeting_id
    _draft(state)['status'] = 'pending_commit'
    state['confirmation_request']=None
    bp['sales_dialogue_states']['state_json']['booking_proposal'] = None
    record(state, 'booking_pending', f'Awaiting transactional assignment for {chosen} {day} {start_time}')
    return {'ok': True, 'pending_commit': True, 'unit_id': chosen, 'date': day,
            'start_time': start_time,
            'notice': 'Pending atomic database confirmation. Do not state that the visit is confirmed or name an advisor yet.'}


def cancel_visit(state: dict, meeting_id: str, reason: str | None = None) -> dict:
    """Record one simulated cancellation with a linked operation receipt."""
    text=str(state.get('_active_source_message_text') or '').strip().lower()
    if not re.search(r'\b(?:cancel|please cancel|call off)\b',text) or re.search(r"\b(?:don.t|do not|not)\s+cancel\b",text):
        return {'error':'A direct cancellation request from the lead is required.'}
    bp=state['bp_episode'];meeting=next((m for m in bp['meetings'] if m['id']==meeting_id and m['status']=='confirmed' and m['lead_id']==bp['ids']['lead_id']),None)
    if not meeting:return {'error':'No active confirmed visit for this meeting_id.'}
    meeting['status']='cancelled';meeting['confirmation_status']='cancelled_by_lead'
    booking=next((b for b in state['booked_visits'] if b['meeting_id']==meeting_id),None)
    if booking:booking['status']='cancelled'
    operation={'id':str(uuid.uuid4()),'company_id':bp['ids']['company_id'],
               'project_id':bp['ids']['project_id'],'conversation_id':bp['ids']['conversation_id'],
               'lead_id':bp['ids']['lead_id'],'proposal_id':str(uuid.uuid4()),
               'meeting_id':meeting_id,'reason':reason,
               'reason_message_id':state.get('_active_source_message_id'),
               'calendar_status':'simulation','notification_status':'simulation',
               'snapshot_json':{'version':'hermes-demo/cancellation/1.0','meeting_id':meeting_id,
                                'original_start_utc':meeting['meeting_time']},
               'created_at':datetime.now(timezone.utc).isoformat()}
    bp['sales_cancellation_operations'].append(operation)
    state['stage']='Visit cancelled · simulated'
    bp['leads']['funnel_stage']='qualified'
    record(state,'cancellation',f'Cancelled simulated visit {meeting_id[:8]}')
    return {'ok':True,'meeting_id':meeting_id,'status':'cancelled',
            'notice':'Only this browser simulation was changed; no real calendar or notification exists.'}


def reschedule_visit(state: dict, original_meeting_id: str, first_name: str, last_name: str,
                     email: str, phone: str, advisor_id: str | None = None, date_str: str = "", start_time: str = "",
                     proposal_id: str = "", unit_id: str | None = None, *, extra_occupied: dict | None = None) -> dict:
    """New meeting replaces previous: old Meeting is cancelled, history preserved."""
    bp=state['bp_episode']
    old=next((m for m in bp['meetings'] if m['id']==original_meeting_id and m['status']=='confirmed'
              and m['lead_id']==bp['ids']['lead_id']),None)
    if old is None:return {'error':'No matching active visit to reschedule.'}
    if unit_id is None:
        prior=next((b for b in state['booked_visits'] if b['meeting_id']==original_meeting_id),None)
        unit_id=prior['unit_id'] if prior else None
    new=book_visit(state,first_name,last_name,email,phone,advisor_id,date_str,start_time,
                   proposal_id,unit_id=unit_id,replaces_meeting_id=original_meeting_id,extra_occupied=extra_occupied)
    if 'error' in new:return new
    old['status']='cancelled';old['confirmation_status']='rescheduled'
    prior=next((b for b in state['booked_visits'] if b['meeting_id']==original_meeting_id),None)
    if prior:prior['status']='rescheduled'
    latest=bp['sales_booking_operations'][-1]
    bp['sales_reschedule_operations'].append({
        'id':str(uuid.uuid4()), 'company_id':bp['ids']['company_id'],
        'project_id':bp['ids']['project_id'],'conversation_id':bp['ids']['conversation_id'],
        'lead_id':bp['ids']['lead_id'],'proposal_id':proposal_id,
        'original_meeting_id':original_meeting_id,
        'replacement_meeting_id':bp['meetings'][-1]['id'],
        'replacement_booking_operation_id':latest['id'],
        'snapshot_json':{'version':'hermes-demo/reschedule/1.0',
                         'original':{'meeting_id':original_meeting_id,'starts_at':old['meeting_time']},
                         'replacement':{'meeting_id':bp['meetings'][-1]['id'],
                                        'starts_at':bp['meetings'][-1]['meeting_time']}},
        'calendar_status':'simulation','created_at':datetime.now(timezone.utc).isoformat()})
    state['stage']='Visit rescheduled · simulated'
    record(state,'reschedule',f'Rescheduled simulated visit to {date_str} {start_time}')
    return {'ok':True,'original_meeting_id':original_meeting_id,
            'replacement_meeting_id':bp['meetings'][-1]['id'], 'booking':state['booked_visits'][-1],
            'notice':'Simulation only; original appointment was cancelled in this session and a replacement was created.'}


def profile_snapshot(state: dict) -> dict:
    """Small, read-only UI projection for in-turn Hermes tool events.

    The complete state and Hermes history never enter live event payloads.
    The server only commits the episode atomically after the agent completes.
    """
    bp = state.get('bp_episode') or {}
    facts = (bp.get('leads') or {}).get('lead_profile_data', {}).get('sales_profile', {}).get('current', {})
    return {
        'lead': dict(state.get('lead') or {}),
        'search_preferences': copy.deepcopy(_preferences(state)),
        'active_search':copy.deepcopy(state.get('active_search')),
        'visit_planning_paused':bool(state.get('visit_planning_paused')),
        'offered_assets':copy.deepcopy(state.get('offered_assets') or {}),
        'property_filters':copy.deepcopy(state.get('property_filters') or property_filters.empty()),
        'recent_preferences_changes': copy.deepcopy(state.get('search_preferences_history', [])[-3:]),
        'confirmation_request': copy.deepcopy(state.get('confirmation_request')),
        'last_offered_date': state.get('last_offered_date'),
        'stage': state.get('stage'),
        'exploring_unit_id': state.get('exploring_unit_id'),
        'conversation_focus': copy.deepcopy(_focus(state)),
        'visit_draft': copy.deepcopy(_draft(state)),
        'last_slot_search': copy.deepcopy(state.get('last_slot_search')),
        'conversation_phase':state.get('conversation_phase','discovery'),
        'last_presented_units':list(state.get('last_presented_units') or []),
        'answered_topics':copy.deepcopy(state.get('answered_topics',[])[-12:]),
        'property_interests': list(state.get('property_interests') or []),
        'ui_fact_evidence': dict(state.get('ui_fact_evidence') or {}),
        'facts': {k: dict(v) for k,v in facts.items()},
        'tool_observations': list(state.get('tool_observations') or [])[-8:],
    }
