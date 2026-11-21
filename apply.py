#!/usr/bin/env python3
"""Apply the V13.2 differential to a LOCAL checkout of testing-hermes-agent.

Read-only preflight on every targeted source file. No GitHub/Vercel access.
Writes a reproducible changes.patch and only touches files named in the diff.
Run: python /path/to/apply.py --repo /path/to/testing-hermes-agent --dry-run
     python /path/to/apply.py --repo /path/to/testing-hermes-agent
"""
from __future__ import annotations
import argparse, difflib, pathlib, sys, hashlib, os

ROOT=pathlib.Path(__file__).resolve().parent

class PatchError(RuntimeError):pass

class Editor:
    def __init__(self,path,text):self.path,self.text,self.count=path,text,0
    def replace(self,needle,replacement,*,n=1):
        seen=self.text.count(needle)
        if seen!=n:raise PatchError(f'{self.path}: expected {n} occurrences, found {seen}: {needle[:110]!r}')
        self.text=self.text.replace(needle,replacement)
        self.count+=1
    def before(self,anchor,insert):self.replace(anchor,insert+anchor)
    def after(self,anchor,insert):self.replace(anchor,anchor+insert)
    def at_function_end(self,head,foot,insert):
        start=self.text.find(head)
        end=self.text.find(foot,start+len(head))
        if start<0 or end<0:raise PatchError(f'{self.path}: function boundary missing')
        self.text=self.text[:end]+insert+self.text[end:];self.count+=1


def commercial(text):
    e=Editor('backend/app/commercial_turn.py',text)
    e.after('import re\n', '\nfrom . import commercial_continuity as continuity\n')
    e.replace('    text=str(message or \'\')\n    if _BOOKING.search(text) and not _SEARCH.search(text):',
              '    text=str(message or \'\')\n    if continuity.is_calendar_question(text):\n        return False\n    if _BOOKING.search(text) and not _SEARCH.search(text):')
    e.replace('def intent(message: str) -> str:\n    text=str(message or \'\')\n    if search_request(text):',
              '''def intent(message: str, state: dict | None = None) -> str:
    text=str(message or '')
    state=state or {}
    draft=state.get('visit_draft') or {}
    if continuity.is_calendar_question(text,has_visit=bool(draft.get('unit_id')),
                                      phase=state.get('conversation_phase','')):
        return 'visit_planning'
    if (draft.get('unit_id') and continuity.explicitly_selected_date(
            text,state.get('last_offered_dates') or [])):
        return 'visit_planning'
    if search_request(text):''')
    e.replace('    if search_request(message):\n        return None\n    return conversation_context.topic(message)',
              '''    if search_request(message) or continuity.is_calendar_question(message):
        return None
    return conversation_context.topic(message)''')
    return e.text


def context(text):
    e=Editor('backend/app/conversation_context.py',text)
    e.replace("_CODE = re.compile(r'(?<![A-Z0-9])H-\\d{3}(?![A-Z0-9])', re.I)",
              "_CODE = re.compile(r'(?<![A-Z0-9])H[- ]?(\\d{3})(?![A-Z0-9])', re.I)")
    e.replace("    return list(dict.fromkeys(code.upper() for code in _CODE.findall(text or '')\n                              if code.upper() in units))",
              "    return list(dict.fromkeys('H-'+m.group(1) for m in _CODE.finditer(text or '')\n                              if 'H-'+m.group(1) in units))")
    e.replace('def resolved_reference(state: dict, text: str, units: dict[str, dict]) -> dict[str, Any]:',
              'def resolved_reference(state: dict, text: str, units: dict[str, dict], *,\n                       prefer_visit: bool = False) -> dict[str, Any]:')
    e.replace("    if len(named)>1:\n        return {'unit_id':None,'reason':'multiple_explicit_units','ambiguous':True,\n                'candidates':named}\n    recent=",
              """    if len(named)>1:
        return {'unit_id':None,'reason':'multiple_explicit_units','ambiguous':True,
                'candidates':named}
    if prefer_visit:
        draft=(state.get('visit_draft') or {})
        if draft.get('unit_id') in units and draft.get('status') not in ('idle','confirmed'):
            return {'unit_id':draft['unit_id'],'reason':'active_visit_draft','ambiguous':False}
    recent=""")
    return e.text


def domain(text):
    e=Editor('backend/app/domain.py',text)
    e.replace('from . import scheduling, sales_preferences, conversation_context, property_filters, commercial_turn, search_sessions',
              'from . import scheduling, sales_preferences, conversation_context, property_filters, commercial_turn, search_sessions, commercial_continuity as continuity')
    e.replace("        'active_search': None,\n", """        'active_search': None,
        'preference_update_proposals': {},
        'last_offered_dates': [],
""")
    e.replace("    draft = _draft(state)\n    if draft.get('unit_id') != code:\n        draft.update(unit_id=code, date=None, start_time=None,proposal_id=None,status='day_needed')",
              """    draft = _draft(state)
    if draft.get('unit_id') != code:
        # Preserve only *preferences* across a house switch; prior slots,
        # offers and consent never transfer to another property.
        pref_day=draft.get('date') or draft.get('preferred_date')
        pref_time=draft.get('start_time') or draft.get('preferred_start_time')
        draft.update(unit_id=code, date=None, start_time=None,proposal_id=None,
                     status='day_needed',preferred_date=pref_day,
                     preferred_start_time=pref_time)
        state['last_offered_dates']=[]""")
    e.replace("    resolution=conversation_context.resolved_reference(state,message,known)\n    state['_turn_property_resolution']=resolution",
              """    draft=_draft(state)
    old_unit=draft.get('unit_id')
    new_visit=continuity.is_new_visit_request(message)
    calendar_followup=(state.get('_turn_intent')=='visit_planning' and
        bool(old_unit) and not new_visit)
    resolution=conversation_context.resolved_reference(
        state,message,known,prefer_visit=calendar_followup)
    # A buyer explicitly naming a house in a calendar question is NOT
    # permission to switch an existing visit unless they request that switch.
    if calendar_followup and old_unit in known and resolution.get('unit_id') != old_unit:
        state['_calendar_reference_conflict']={
            'current_visit_unit_id':old_unit,
            'mentioned_unit_id':resolution.get('unit_id')}
        resolution={'unit_id':old_unit,'reason':'active_visit_guard','ambiguous':False}
    state['_turn_property_resolution']=resolution""")
    e.replace("    if visit_intent and not negated and resolution.get('unit_id') and not resolution.get('ambiguous'):\n        _set_visit_unit(state,resolution['unit_id'])\n        state['conversation_phase']='visit_planning'",
              """    if (visit_intent and not negated and resolution.get('unit_id') and
            not resolution.get('ambiguous') and
            (not old_unit or new_visit or continuity.explicit_visit_switch(
                message,resolution.get('unit_id'),old_unit))):
        # Follow-up hours/days never silently switch house.
        _set_visit_unit(state,resolution['unit_id'])
        state['conversation_phase']='visit_planning'
    elif calendar_followup and old_unit:
        state['conversation_phase']='visit_planning'""")
    e.replace("    state['_turn_intent']=commercial_turn.intent(message)",
              "    state['_turn_intent']=commercial_turn.intent(message,state)")
    e.replace("    dialogue=state['bp_episode']['sales_dialogue_states']['state_json']\n    memory=dialogue.setdefault('memory',{})",
              """    if topic in ('gallery','documents') and unit_id in _units_by_code():
        approved=state.get('_approved_media_urls_this_turn') or []
        if any(url in reply for url in approved):
            state.setdefault('answered_topics',[]).append({
                'unit_id':unit_id,'topic':topic,
                'source_message_id':state.get('_active_source_message_id'),
                'reply_message_id':state['bp_episode']['sales_messages'][-1]['id'],
                'recorded_at':now_local().isoformat()})
            state['answered_topics']=state['answered_topics'][-80:]
    dialogue=state['bp_episode']['sales_dialogue_states']['state_json']
    memory=dialogue.setdefault('memory',{})""")
    e.replace("    _observe_inbound_context(state, message)\n    bp['sales_messages'].append({",
              """    _observe_inbound_context(state, message)
    # Store only *explicit* property interest, even if Hermes omits a tool.
    # An assistant's last mention is never evidence of lead interest.
    resolution=state.get('_turn_property_resolution') or {}
    code=resolution.get('unit_id')
    if code in _units_by_code() and not resolution.get('ambiguous'):
        declared=bool(re.search(r"\\b(?:i(?: am|'m)? interested|i(?: would|'d)? like to visit|"
                                r"i want to visit|i'd like to tour|schedule a visit)\\b",message,re.I))
        if declared:
            status='visit_requested' if continuity.is_new_visit_request(message) else 'interested'
            continuity.register_interest(state,_units_by_code()[code],
                source_message_id=state.get('_active_source_message_id'),status=status)
            continuity.maybe_propose_preference_update(state,_units_by_code()[code],
                source_message_id=state.get('_active_source_message_id'))
    bp['sales_messages'].append({""")
    # Do not falsely convert old proposal into fresh visit when only partial hour accepted.
    e.replace("    day=state.get('last_offered_date')\n    # An explicitly specified YYYY-MM-DD may override the last offered day.",
              """    day=state.get('last_offered_date')
    # A buyer accepting a single hour after multiple *days* were presented
    # has not selected a day. Record the time as a preference only.
    offered_days=state.get('last_offered_dates') or []
    if (len(set(offered_days))>1 or _draft(state).get('date_context_ambiguous')) and not continuity.explicitly_selected_date(text,offered_days):
        _draft(state)['preferred_start_time']=times[0]
        _draft(state)['preferred_date']=_draft(state).get('date') or _draft(state).get('preferred_date')
        _draft(state).update(date=None,start_time=None,proposal_id=None,status='day_needed')
        state['confirmation_request']=None
        return {'ok':True,'captured':False,'preferred_start_time':times[0],
                'day_needed':True}
    # An explicitly specified YYYY-MM-DD may override the last offered day.""")
    e.replace("    # An explicitly specified YYYY-MM-DD may override the last offered day.\n    specified=re.search(r'\\b20\\d{2}-\\d{2}-\\d{2}\\b',text)\n    if specified:day=specified.group(0)",
              """    # Choose a date mentioned in the offered dates, never a stale one.
    selected_day=continuity.explicitly_selected_date(text,offered_days)
    if selected_day:day=selected_day
    specified=re.search(r'\\b20\\d{2}-\\d{2}-\\d{2}\\b',text)
    if specified:day=specified.group(0)""")
    e.replace("    if proposal.get('selected_unit_id')!=_draft(state).get('unit_id'):\n        return {'error':'This offer belongs to a different property from the current visit draft.'}",
              """    if proposal.get('selected_unit_id')!=_draft(state).get('unit_id'):
        return {'error':'This offer belongs to a different property from the current visit draft.'}
    offered_days=state.get('last_offered_dates') or []
    if (len(set(offered_days))>1 or _draft(state).get('date_context_ambiguous')) and not continuity.explicitly_selected_date(
            str(state.get('_active_source_message_text') or ''),offered_days):
        return {'error':'The buyer has chosen a preferred hour but not a date.',
                'error_code':'VISIT_DAY_REQUIRED',
                'next_action':'Ask only which day to use. Never infer the prior date.'}""")
    e.replace("    state['confirmation_request']=None\n    state['bp_episode']['sales_dialogue_states']['state_json']['booking_proposal'] = copy.deepcopy(proposal)",
              """    state['confirmation_request']=None
    state['last_offered_dates']=[parsed_day]
    state['bp_episode']['sales_dialogue_states']['state_json']['booking_proposal'] = copy.deepcopy(proposal)""")
    e.replace("    pol = scheduling.policy()\n    today = scheduling.local_now().date()\n", """    pol = scheduling.policy()
    today = scheduling.local_now().date()
""")  # syntactic guard
    e.replace("def slots(state: dict, date_str: str | None = None, *,\n          time_preference: str | None = None, extra_occupied: dict | None = None) -> dict:",
              """def slots(state: dict, date_str: str | None = None, *,
          time_preference: str | None = None,
          requested_start_time: str | None = None,
          extra_occupied: dict | None = None) -> dict:""")
    e.replace("    spoken = str(state.get('_active_source_message_text') or '').lower()\n    if time_preference is None:",
              """    spoken = str(state.get('_active_source_message_text') or '').lower()
    if date_str is None and state.get('_turn_intent')=='visit_planning':
        last_requested=(state.get('last_slot_search') or {}).get('requested')
        if last_requested in ('next week','this week','this weekend','next weekend'):
            date_str=last_requested
    if requested_start_time is None and state.get('_turn_intent')=='visit_planning':
        requested_start_time=continuity.clock_value(spoken)
    if requested_start_time is not None:
        try:
            requested_start_time=time.fromisoformat(requested_start_time).strftime('%H:%M')
        except (TypeError,ValueError):
            return _slot_error('INVALID_START_TIME',
                'Choose a real clock time, for example 08:00.',unit_id,
                next_action='Clarify only the requested clock time.')
    if time_preference is None:""")
    e.replace("        if not low <= exact_date <= high:\n", """        if (draft.get('date_context_ambiguous') and
                len(set(state.get('last_offered_dates') or []))>1 and
                continuity.explicitly_selected_date(
                    spoken,state.get('last_offered_dates') or [])!=exact_date.isoformat()):
            return _slot_error('VISIT_DAY_REQUIRED',
                'The buyer accepted an hour but has not selected a day.',
                unit_id,next_action='Ask which day to use; do not reuse an older exact date.')
        if not low <= exact_date <= high:
""")
    e.replace("    relative = _relative_range(date_str, today) if date_str else None",
              """    # 'Other days that week' must search the whole anchored week,
    # never just the old explicit day accidentally passed by the model.
    if re.search(r'\\b(?:other|remaining|different)\\s+(?:days?|dates?)\\b',spoken,re.I):
        anchor=state.get('last_offered_date') or draft.get('preferred_date') or draft.get('date')
        if anchor:
            try:
                anchor_day=date.fromisoformat(anchor)
                date_str=('next week' if anchor_day.isocalendar()[:2]!=today.isocalendar()[:2]
                          else 'this week')
            except ValueError:
                pass
    relative = _relative_range(date_str, today) if date_str else None""")
    e.replace("    key = json.dumps([unit_id, date_str, time_preference], sort_keys=True)",
              "    key = json.dumps([unit_id, date_str, time_preference, requested_start_time], sort_keys=True)")
    e.replace("    options = scheduling.public_start_times([x for x in all_slots if within(x)])\n    if exact_date is None:\n        days = []\n        for day_value in sorted({x['date'] for x in options})[:4]:",
              """    all_period=scheduling.public_start_times([x for x in all_slots if within(x)])
    options=([x for x in all_period if x['start_time']==requested_start_time]
             if requested_start_time else all_period)
    if exact_date is None:
        days = []
        for day_value in sorted({x['date'] for x in options}):""")
    e.replace("        if not days:\n            result = _slot_error('NO_SLOTS',\n                'No visit dates match the requested period for ' + unit_id + '.',\n                unit_id, next_action='Ask for another period or time of day; keep the house context.')\n        else:\n            result = {'ok': True, 'unit_id': unit_id, 'available_dates': days,\n                       'proposal_id': None, 'timezone': pol['timezone'],\n                       'next_action': 'Ask which date the buyer prefers for ' + unit_id +\n                                      ', then search that exact day for hours.'}",
              """        if not days and requested_start_time:
            earliest=min((s['start_time'] for s in all_period),default=None)
            result={'ok':True,'status':'no_slots_at_requested_time',
                    'unit_id':unit_id,'requested_start_time':requested_start_time,
                    'requested_period':{'start':range_start.isoformat(),
                                        'end':range_end.isoformat()},
                    'available_dates':[],'earliest_available_start':earliest,
                    'proposal_id':None,'timezone':pol['timezone'],
                    'next_action':'Explain that the requested hour is unavailable across the entire period; offer a verified alternative hour.'}
        elif not days:
            result = _slot_error('NO_SLOTS',
                'No visit dates match the requested period for ' + unit_id + '.',
                unit_id, next_action='Ask for another period or time of day; keep the house context.')
        else:
            result = {'ok': True, 'unit_id': unit_id, 'available_dates': days,
                       'status':'available','requested_start_time':requested_start_time,
                       'requested_period':{'start':range_start.isoformat(),
                                           'end':range_end.isoformat()},
                       'proposal_id': None, 'timezone': pol['timezone'],
                       'next_action': 'Ask which date the buyer prefers for ' + unit_id +
                                      ', then search that exact day for hours.'}
        state['last_offered_dates']=[d['date'] for d in days]
        # Broad period searches invalidate an older *chosen* day without
        # discarding it as a preference. No old proposal can be confirmed.
        draft['date_context_ambiguous']=True
        if draft.get('date') and state.get('_turn_intent')=='visit_planning':
            draft['preferred_date']=draft['date']
            draft.update(date=None,start_time=None,proposal_id=None,status='day_needed')
            state['confirmation_request']=None
        if requested_start_time:
            draft['preferred_start_time']=requested_start_time""")
    e.replace("                                     'time_preference': time_preference,\n                                     'result_code': 'OK' if result.get('ok') else 'NO_SLOTS'}",
              """                                     'time_preference': time_preference,
                                     'requested_start_time':requested_start_time,
                                     'result_code':('NO_SLOTS_AT_EXACT_TIME' if result.get('status')=='no_slots_at_requested_time'
                                                    else 'OK' if result.get('ok') else 'NO_SLOTS')}""")
    e.replace("    if not options:\n        # A failed exploratory search must not delete a previously selected hour.",
              """    if not options and requested_start_time:
        earliest=min((s['start_time'] for s in all_period),default=None)
        result={'ok':True,'status':'no_slots_at_requested_time',
                'unit_id':unit_id,'date':exact_date.isoformat(),
                'requested_start_time':requested_start_time,
                'earliest_available_start':earliest,'available_slots':[],
                'proposal_id':None,'timezone':pol['timezone']}
        state['last_slot_search']={'unit_id':unit_id,'requested':date_str,
            'date':exact_date.isoformat(),'requested_start_time':requested_start_time,
            'result_code':'NO_SLOTS_AT_EXACT_TIME'}
        queries[key]=copy.deepcopy(result)
        return result
    if not options:
        # A failed exploratory search must not delete a previously selected hour.""")
    e.replace("    state['last_offered_date'] = exact_date.isoformat()\n", "    state['last_offered_date'] = exact_date.isoformat()\n    state['last_offered_dates']=[exact_date.isoformat()]\n    draft['date_context_ambiguous']=False\n")
    # Ignore active_search in a detail/booking screen but preserve historical data.
    e.replace("        'active_search':copy.deepcopy(state.get('active_search')),\n", """        'active_search':copy.deepcopy(state.get('active_search')),
        'preference_update_proposals':copy.deepcopy(state.get('preference_update_proposals') or {}),
        'last_offered_dates':list(state.get('last_offered_dates') or []),
""")
    e.replace("    state.pop('_turn_property_resolution', None)\n", """    state.pop('_turn_property_resolution', None)
    state.pop('_calendar_reference_conflict', None)
""")
    # Offer-response projection: buyer consent to a suggested profile change
    # uses the same canonical BP fact/evidence format as a direct declaration.
    e.before("def _selected_offer(state: dict) -> dict | None:", """def _sync_preference_projection(state:dict,result:dict,message:str) -> None:
    if not result.get('ok') or not result.get('changed_fields'):
        return
    facts={}
    prefs=_preferences(state)
    if 'budget' in result['changed_fields']:
        budget=prefs['budget']
        if budget.get('maximum_usd') is not None:
            facts['budget_maximum']=budget['maximum_usd']
        facts['budget']=f"USD {budget.get('minimum_usd') or 'unset'} to {budget.get('maximum_usd') or 'unset'} ({budget['mode']})"
        state.setdefault('ui_fact_evidence',{})['budget_usd']={
            'source_message_id':state.get('_active_source_message_id'),
            'evidence':message,'captured_at':now_local().isoformat(),
            'source':'active_conversation'}
    if 'bedrooms' in result['changed_fields']:
        beds=prefs['bedrooms']
        facts['bedrooms']=next((n for n in (beds['preferred'],beds['minimum'],beds['maximum'])
                               if n is not None),None)
        state.setdefault('ui_fact_evidence',{})['min_bedrooms']={
            'source_message_id':state.get('_active_source_message_id'),
            'evidence':message,'captured_at':now_local().isoformat(),
            'source':'active_conversation'}
    _capture_bp_facts(state,facts)
    state['stage']='Preferences updated'
    record(state,'preferences_updated',
           f"Buyer accepted offered changes to {', '.join(result['changed_fields'])}")


""")

    e.replace("    rank = {'discussed': 0, 'considering': 1, 'interested': 2}",
              "    rank = {'discussed': 0, 'considering': 1, 'interested': 2, 'visit_requested': 3}")
    # auto-registered visit_requested cannot be overwritten by an older generic
    # track_property_interest call from Hermes.
    # Preserve phase on a detail request after any harmless search history.
    return e.text


def policy(text):
    e=Editor('backend/app/delivery_policy.py',text)
    e.after('from . import sms\n','from . import commercial_continuity as continuity\n')
    e.before('def validate_outbound(reply: str, state: dict) -> str:', '''def _safe_location_reply(state:dict) -> str:
    from . import domain
    ref=state.get('_turn_property_resolution') or {}
    code=ref.get('unit_id') or (state.get('visit_draft') or {}).get('unit_id')
    unit=domain._units_by_code().get(code) if code else None
    if not unit:
        return 'I can share the published location once we have a specific house in mind.'
    loc=unit.get('location') or {}
    city=loc.get('city') or 'Lima'
    # This specific public city reference is from the approved catalogue.
    map_link=(loc.get('maps_url') if loc.get('map_scope')=='city_reference_only'
              and loc.get('verification_status')=='synthetic' else None)
    return (f'{code} is listed in {city}, Peru (synthetic demo). '
            'We have only a city-level reference, not a verified address '
            'or visit pin.'+
            (f' Illustrative city map: {map_link}' if map_link else ''))


def _safe_url_recovery(state:dict, plain:str, urls:list[str]) -> str:
    """Fail closed on links while keeping a harmless, useful buyer reply."""
    topic=(state.get('_last_inbound_topic') or '')
    question=str(state.get('_active_source_message_text') or '')
    if topic=='location' or re.search(r'\\b(?:address|location|maps?|where is|directions)\\b',question,re.I):
        return _safe_location_reply(state)
    safe=plain
    for link in urls:
        safe=safe.replace(link,'')
    safe=re.sub(r'\\s{2,}',' ',safe).strip(' ,;:-')
    if not safe or re.search(r'\\b(?:click here|download here|use this link|follow this link)\\b|^\\s*(?:download|click|link)\\s*$',safe,re.I):
        return ('I can share the approved property details or media. '
                'Which resource would you like?')
    # Don't let an arbitrary URL that was stripped imply a verified address.
    return safe


def _offer_preferences_after_interest(state:dict,plain:str) -> str:
    from . import domain
    code=(state.get('_turn_property_resolution') or {}).get('unit_id')
    if not code or state.get('_turn_intent') in ('property_search','visit_planning','booking_action'):
        return plain
    unit=domain._units_by_code().get(code)
    if not unit:return plain
    advisory=continuity.preference_offer_text(state,unit)
    if not advisory:return plain
    # One question about relevant criteria, never a blocking confirmation.
    if len(plain)>310:
        return plain
    offer=state['preference_update_proposals'][code]
    offer['status']='offered'
    offer['offered_message_id']=state.get('_active_source_message_id')
    return plain.rstrip()+' '+advisory


''')
    e.replace("        # Do not turn a bad link into a plausible but unverifiable address.\n        raise ValueError('Hermes returned a URL not authorized by the domain media tool.')",
              """        # An unauthorized location/media URL must never destroy a valid
        # turn or produce a fictional address. Strip and answer from verified
        # catalog facts instead; unknown domains are never whitelisted.
        plain=_safe_url_recovery(state,plain,unauthorised)
        state.setdefault('tool_observations',[]).append({
            'tool':'outbound_url_recovery','status':'recovered',
            'fields':['unapproved_url'],
            'message_id':state.get('_active_source_message_id'),
            'timestamp':__import__('datetime').datetime.now().isoformat()})""")
    e.before("    # The language model cannot promote a selected hour to a confirmed visit.\n", """    # Repair a stray house mention in visit planning WITHOUT choosing it.
    # A reply cannot transfer the buyer's draft merely by naming another house.
    if state.get('_turn_intent')=='visit_planning':
        from . import domain, conversation_context
        draft=state.get('visit_draft') or {}
        visit_house=draft.get('unit_id')
        if visit_house in domain._units_by_code():
            mentioned=conversation_context.mentioned_codes(plain,domain._units_by_code())
            if mentioned and visit_house not in mentioned:
                last=state.get('last_slot_search') or {}
                preferred=draft.get('preferred_start_time')
                if last.get('result_code')=='NO_SLOTS_AT_EXACT_TIME':
                    requested=last.get('requested_start_time') or preferred
                    plain=(f'For {visit_house}, there are no visits at '
                           f'{requested or "that hour"} in the requested period. '
                           'Would you like me to check 9:00 AM instead?')
                elif draft.get('status')=='day_needed' and preferred:
                    plain=(f'For {visit_house}, {preferred} is your preferred time. '
                           'Which date would you like to check?')
                else:
                    plain=(f'We are arranging a visit to {visit_house}. '
                           'Which day or time would you like to check?')
                state.setdefault('tool_observations',[]).append({
                    'tool':'visit_house_reply_recovery','status':'recovered',
                    'fields':['visit_draft.unit_id'],
                    'message_id':state.get('_active_source_message_id'),
                    'timestamp':__import__('datetime').datetime.now().isoformat()})
""")
    e.replace("    return _suggest_available_assets(state,plain)",
              "    return _offer_preferences_after_interest(state,_suggest_available_assets(state,plain))")
    return e.text


def hermes(text):
    e=Editor('backend/app/hermes_runtime.py',text)
    e.replace("    'get_available_visit_slots': {\n        'description': 'Search anonymous visit availability for a previously requested house. Exact dates create expiring offers; broad periods return available days, never booked hours. Never repeat an identical failed query.',",
              """    'get_available_visit_slots': {
        'description': 'Search the FULL requested week/day range and optionally an exact clock start (HH:MM). Date-only searches return days, not booking consent. A no-slots result at 08:00 is a valid answer. Keep the existing visit house.',""")
    e.replace("            'time_preference': {'type': 'string', 'enum': ['any','morning','afternoon','evening'],\n                                'description': 'Only when requested. Morning before 12:00, afternoon 12:00-17:00, evening after 17:00.'},",
              """            'time_preference': {'type': 'string', 'enum': ['any','morning','afternoon','evening'],
                                'description': 'Only when requested. Morning before 12:00, afternoon 12:00-17:00, evening after 17:00.'},
            'requested_start_time': {'type':'string',
                'description':'Optional exact time HH:MM, e.g. 08:00, across the full selected period.'},""")
    e.replace("- Explore date first, then exact available times. Mention the house naturally.",
              """- Explore date first, then exact available times. Mention the house naturally.
- 'Other days this week' means CALENDAR dates, never more houses. Keep the
  selected visit house independent of any last advertised search alternative.
  Check ALL days in the requested week for an exact 08:00 start when asked;
  do not limit the answer to a previously selected single day.
- After listing several dates, '9am works' accepts a preferred hour only;
  request the missing DATE, never silently reuse an old last_offered_date.
- An interest in a home above the buyer's usual cap is a valid exception, NOT
  authorization to raise the cap. Briefly offer to update only the relevant
  preferences once; proceed with photos/details/visit even without an answer.
  Only a clear buyer acceptance of an offered preference change may persist it.
- Do not invent street addresses. Map links in this demo are city-level only.
  When the buyer asks for a location, disclose that no verified visit pin exists.
- Never use an old proposal_id for a different property and never call booking
  tools for bare 'OK' or accepting a clock time. Validation errors are internal.""")
    e.replace("        'active_search':state.get('active_search'),\n", """        'active_search':state.get('active_search'),
        'preference_update_proposals':state.get('preference_update_proposals'),
        'last_offered_dates':state.get('last_offered_dates'),
        'property_interests':state.get('property_interests'),
""")
    return e.text


def main(text):
    e=Editor('backend/main.py',text)
    e.replace("    verified = domain.verify_explicit_preferences(state, message)\n", """    # Accept a previously OFFERED preference change only when the buyer
    # explicitly chooses it. This is not inferred from selecting a house.
    from app import commercial_continuity as continuity, sales_preferences
    decided=continuity.preference_offer_decision(
        state,message,domain._units_by_code())
    if decided.get('action')=='update':
        offer=decided['offer']; changes=offer['suggested_changes']
        revision={}
        if 'budget' in decided['fields']:
            revision['budget']={
                'mode':'cap',
                'maximum_usd':changes['budget_maximum_usd']['suggested']}
        if 'bedrooms' in decided['fields']:
            revision['bedrooms']={
                'mode':'preferred','preferred':changes['bedrooms']['suggested']}
        changed=sales_preferences.apply(state,revision,
            source_message_id=state.get('_active_source_message_id'))
        if changed.get('error'):
            raise ValueError(changed['error'])
        # Rebuild UI/BlackPenguin profile from the same source evidence.
        domain._sync_preference_projection(state,changed,message)
        if 'filters' in decided['fields']:
            from app import property_filters
            filter_changes={k:v['suggested'] for k,v in
                (offer.get('suggested_filter_changes') or {}).items()}
            filtered=property_filters.update(state,filter_changes,
                state.get('_active_source_message_id'))
            if filtered.get('error'):
                raise ValueError(filtered['error'])
            state.setdefault('tool_observations',[]).append({
                'tool':'accepted_property_filters','status':'applied',
                'fields':filtered.get('changed_fields') or [],
                'message_id':state.get('_active_source_message_id'),
                'timestamp':domain.now_local().isoformat()})
        state['_turn_intent']='preference_update'
        state['_turn_scope']='permanent'
        offer['status']='accepted'
        offer['accepted_fields']=list(decided['fields'])
        offer['decision_message_id']=state.get('_active_source_message_id')
        state['conversation_phase']='exploration'
    elif decided.get('action')=='exception_only':
        state.setdefault('tool_observations',[]).append({
            'tool':'preference_exception','status':'applied',
            'fields':['unit_id'],
            'message_id':state.get('_active_source_message_id'),
            'timestamp':domain.now_local().isoformat()})
    verified = domain.verify_explicit_preferences(state, message)
""")
    return e.text


def frontend(text):
    e=Editor('frontend/src/app/page.tsx',text)
    e.replace('type ActiveSearch={revision:number;scope:\'temporary\'|\'permanent\';criteria:Record<string,unknown>;matching_unit_ids:string[];source_message_id:string|null;created_at:string};',
              """type ActiveSearch={revision:number;scope:'temporary'|'permanent';criteria:Record<string,unknown>;matching_unit_ids:string[];source_message_id:string|null;created_at:string};
type PreferenceProposal={unit_id:string;primary_revision:number;status:string;suggested_changes:Record<string,{current:number;suggested:number;excess_usd?:number}>;suggested_filter_changes?:Record<string,{current:number|string|boolean;suggested:number|string|boolean}>};""")
    e.replace('type State={active_search?:ActiveSearch|null;',
              'type State={preference_update_proposals?:Record<string,PreferenceProposal>;last_offered_dates?:string[];active_search?:ActiveSearch|null;')
    e.replace("type ProfilePreview={lead:State['lead'];stage:string;active_search?:ActiveSearch|null;",
              "type ProfilePreview={lead:State['lead'];stage:string;preference_update_proposals?:Record<string,PreferenceProposal>;last_offered_dates?:string[];active_search?:ActiveSearch|null;")
    e.replace('  const activeSearch=liveProfile?.active_search||state?.active_search;',
              """  const activeSearch=liveProfile?.active_search||state?.active_search;
  const preferenceProposals=liveProfile?.preference_update_proposals||state?.preference_update_proposals||{};
  const latestPreferenceProposal=Object.values(preferenceProposals).find(p=>
    p.unit_id===(liveProfile?.conversation_focus?.unit_id||state?.conversation_focus?.unit_id)&&
    (p.status==='pending'||p.status==='offered'||p.status==='exception_only'));
  const showSearchAsCurrent=conversationPhase==='exploration'||conversationPhase==='search'||conversationPhase==='comparison';""")
    e.replace('              {activeSearch&&<div className="active-search-panel">',
              '              {activeSearch&&showSearchAsCurrent&&<div className="active-search-panel">')
    e.replace("              {planningPaused&&visitDraft?.unit_id&&<div className=\"paused-visit-note\">",
              """              {activeSearch&&!showSearchAsCurrent&&<details className="inspector">
                <summary>Previous search · #{activeSearch.revision}</summary>
                <small className="muted">{activeSearch.matching_unit_ids.length?
                  'Earlier matches: '+activeSearch.matching_unit_ids.join(', '):
                  'That earlier search had no exact matches. It is not the current activity.'}</small>
              </details>}
              {latestPreferenceProposal&&<div className="active-search-panel">
                <div className="active-search-head"><Search size={13}/><strong>Preference advice · {latestPreferenceProposal.unit_id}</strong></div>
                <small>{latestPreferenceProposal.status==='exception_only'?'One-off option; original preferences retained':
                  latestPreferenceProposal.status==='offered'?'Update offered; buyer has not accepted':'Advice pending'}</small>
                {latestPreferenceProposal.suggested_changes.budget_maximum_usd&&
                  <small>Price {usd.format(latestPreferenceProposal.suggested_changes.budget_maximum_usd.suggested)}
                    · {usd.format(latestPreferenceProposal.suggested_changes.budget_maximum_usd.excess_usd||0)} over saved maximum</small>}
                {latestPreferenceProposal.suggested_changes.bedrooms&&
                  <small>{latestPreferenceProposal.suggested_changes.bedrooms.suggested} bedrooms vs saved preference of {latestPreferenceProposal.suggested_changes.bedrooms.current}</small>}
                {Object.keys(latestPreferenceProposal.suggested_filter_changes||{}).length>0&&
                  <small>Other differences: {Object.keys(latestPreferenceProposal.suggested_filter_changes||{}).map(s=>s.replaceAll('_',' ')).join(', ')}</small>}
              </div>}
              {planningPaused&&visitDraft?.unit_id&&<div className="paused-visit-note">""")
    e.replace("{o.status==='rejected'?<Info size={13} className=\"agent-work-error\"/>:liveProfile?<Info size={13} className=\"agent-work-pending\"/>:<Check size={13} className=\"agent-work-done\"/>}",
              """{['rejected','cached_rejection'].includes(o.status)?<Info size={13} className="agent-work-error"/>:
                  ['recovered','cached'].includes(o.status)||liveProfile?<Info size={13} className="agent-work-pending"/>:
                  <Check size={13} className="agent-work-done"/>}""")
    e.replace("{o.status==='rejected'&&o.reason&&<small title={o.next_action||undefined}>{o.reason}</small>}",
              "{['rejected','cached_rejection','recovered'].includes(o.status)&&o.reason&&<small title={o.next_action||undefined}>{o.reason}</small>}")
    return e.text

TRANSFORM={
'backend/app/commercial_turn.py':commercial,
'backend/app/conversation_context.py':context,
'backend/app/domain.py':domain,
'backend/app/delivery_policy.py':policy,
'backend/app/hermes_runtime.py':hermes,
'backend/main.py':main,
'frontend/src/app/page.tsx':frontend,
}


def transform(repo:pathlib.Path):
    old,new={},{}
    for path,fn in TRANSFORM.items():
        src=repo/path
        if not src.is_file():raise PatchError(f'Missing {path}: use a full v13.1 checkout')
        raw=src.read_text(encoding='utf-8')
        old[path]=raw
        new[path]=fn(raw)
    for path in ('backend/app/commercial_continuity.py','backend/tests/test_v13_2_continuity.py','README.md'):
        src=repo/path
        old[path]=src.read_text(encoding='utf-8') if src.exists() else ''
        new[path]=(ROOT/path).read_text(encoding='utf-8')
    return old,new


def main_cmd():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',required=True,type=pathlib.Path)
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--print-diff',action='store_true')
    args=ap.parse_args()
    repo=args.repo.resolve()
    if not (repo/'backend/app/domain.py').is_file():
        ap.error('--repo must be the root of a local testing-hermes-agent checkout')
    try:old,new=transform(repo)
    except PatchError as exc:sys.exit('PRECHECK FAILED (nothing changed): '+str(exc))
    patch=''.join(''.join(difflib.unified_diff(
        old[path].splitlines(keepends=True),new[path].splitlines(keepends=True),
        fromfile=('a/'+path if old[path] else '/dev/null'),
        tofile='b/'+path)) for path in sorted(new))
    if args.print_diff:print(patch)
    files=[k for k in new if new[k]!=old[k]]
    print('Preflight successful. Differential files:',len(files))
    for k in files:print(' - '+k)
    if args.dry_run:
        print('Dry run: no files changed.');return
    # All preflight checks passed before the first write.
    for k in files:
        dest=repo/k;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(new[k],encoding='utf-8')
    out=repo/'v13_2_changes.patch'
    out.write_text(patch,encoding='utf-8')
    print('Local files updated. Patch saved:',out)
    print('No GitHub or Vercel actions were executed.')

if __name__=='__main__':main_cmd()
