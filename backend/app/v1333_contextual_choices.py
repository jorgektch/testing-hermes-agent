"""v13.3.3 Upload Ready: install typed choice handling after v13.3.2.

Integrates with existing Hermes + FastAPI + domain APIs. No installer, network
mutation, schema migration, additional model, or real-world SMS sends.
"""
from __future__ import annotations

import copy
import functools
import re
from typing import Any

from . import contextual_choices as choices

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import commercial_turn, conversation_context, domain, delivery_policy, hermes_runtime, v1332_upload_ready

    # v13.3.2 is already installed by app/__init__.py. Do not recursively
    # reinstall or replace its verified property-menu and SMS contracts.
    prev_intent = commercial_turn.intent

    @functools.wraps(prev_intent)
    def intent(message: str, state: dict | None = None) -> str:
        live_state = state if state is not None else v1332_upload_ready._ACTIVE_TURN.get()
        resolution = (live_state or {}).get('_turn_choice_resolution')
        if resolution:
            kind=resolution['type']
            if kind == 'property':
                return prev_intent(message, state=live_state)
            return {'visit_day':'visit_day_selection','visit_time':'visit_time_selection',
                    'media':'media_selection','generic':'contextual_choice'}[kind]
        return prev_intent(message, state=live_state)

    commercial_turn.intent = intent

    previous_resolution = conversation_context.resolved_reference

    @functools.wraps(previous_resolution)
    def resolved_reference(state: dict, text: str, units: dict) -> dict:
        selected = state.get('_turn_choice_resolution') or {}
        if selected.get('type') in ('visit_day','visit_time','media','generic'):
            code=(selected.get('unit_id') or
                  (state.get('conversation_focus') or {}).get('unit_id'))
            if code not in units and selected.get('type')=='generic':
                recent=[x for x in (state.get('last_presented_units') or []) if x in units]
                code=recent[0] if len(recent)==1 else None
            return {'unit_id':code if code in units else None,
                    'reason':'typed_choice_reference','ambiguous':False}
        return previous_resolution(state,text,units)

    conversation_context.resolved_reference = resolved_reference

    previous_blank = domain.blank_state

    @functools.wraps(previous_blank)
    def blank_state() -> dict:
        state=previous_blank()
        state.setdefault('last_presented_choice_set',None)
        state.setdefault('choice_set_history',[])
        state['choice_set_initialized']=True
        return state

    domain.blank_state = blank_state

    previous_begin = domain.begin_turn

    @functools.wraps(previous_begin)
    def begin_turn(state: dict, message: str) -> None:
        state.setdefault('last_presented_choice_set',None)
        selected=choices.resolve(state,message)
        if selected is not None:
            state['_turn_choice_resolution']=selected
        previous_begin(state,message)
        if not selected:
            return
        kind=selected['type']
        if kind == 'property':
            return   # The pre-existing property selection contract owns this.
        if selected.get('status') != 'resolved':
            state['_choice_failure_this_turn']='ambiguous'
            return
        picked=selected['choice']
        state['_selected_choice_this_turn']={
            'type':kind,'index':picked.get('index'),'label':picked.get('label'),
            'source_reply_message_id':(selected.get('menu') or {}).get('source_reply_message_id'),
        }
        unit=selected.get('unit_id')
        if kind == 'visit_day':
            draft=domain._draft(state)
            if draft.get('unit_id')!=unit or draft.get('status') in ('idle','confirmed'):
                state['_choice_failure_this_turn']='visit_context_missing'
                return
            # Choosing a DAY only. Never set an hour, proposal, or booking.
            draft.update(date=picked['date'],start_time=None,proposal_id=None,status='hour_needed')
            state['confirmation_request']=None
            state['pending_visit_offer']=None
            state['conversation_phase']='visit_planning'
            state['visit_planning_paused']=False
            state['_selected_choice_this_turn']['date']=picked['date']
            state['bp_episode']['sales_dialogue_states']['state_json']['visit_draft']=copy.deepcopy(draft)
        elif kind == 'visit_time':
            draft=domain._draft(state)
            proposal_id=picked.get('proposal_id')
            proposal=(state.get('booking_offers') or {}).get(proposal_id) or {}
            if (draft.get('unit_id')!=unit or proposal.get('selected_unit_id')!=unit or
                    proposal.get('date')!=picked.get('date') or
                    proposal.get('status')!='offered'):
                state['_choice_failure_this_turn']='offer_not_active'
                return
            # Existing select_visit_time validates expiry, exact proposed time,
            # unit, and buyer evidence. The ordinal is an explicit clock choice
            # ONLY when linked to the delivered, typed verified time menu.
            original=state.get('_active_source_message_text')
            try:
                state['_active_source_message_text']=(str(original or '')+' '+
                    picked['date']+' '+picked['start_time'])
                result=domain.select_visit_time(state,proposal_id,picked['date'],picked['start_time'])
            finally:
                state['_active_source_message_text']=original
            state['_choice_time_result_this_turn']=result
            if not result.get('ok'):
                state['_choice_failure_this_turn']='offer_expired_or_invalid'
        elif kind == 'media':
            # The media link is selected for reading, not a purchase or booking.
            state['pending_media_offer']={'unit_id':unit,'kind':picked['kind']}
            state['_selected_choice_this_turn']['kind']=picked['kind']
            state['conversation_phase']='property_details'
        else:
            # The LLM may discuss a generic option, but a generic label has NO
            # authority to change preferences, send assets or book anything.
            state['conversation_focus']=dict(state.get('conversation_focus') or {})
            state['conversation_focus']['choice_selection']={
                'label':picked.get('label'),'index':picked.get('index'),'type':'generic'}

    domain.begin_turn = begin_turn

    previous_verify = domain.verify_explicit_preferences

    @functools.wraps(previous_verify)
    def verify_explicit_preferences(state: dict, message: str) -> dict:
        if state.get('_turn_intent') in ('visit_day_selection','visit_time_selection',
                                          'media_selection','contextual_choice'):
            return {'ok':True,'changed_fields':{},'note':'Verified contextual choice; no preference revision.'}
        return previous_verify(state,message)

    domain.verify_explicit_preferences = verify_explicit_preferences

    # Date/time tool outcomes are the source of truth for selectable lists.
    # Never interpret an LLM-authored date or time as a verified slot.
    previous_slots = domain.slots

    @functools.wraps(previous_slots)
    def slots(state: dict, *args: Any, **kwargs: Any) -> dict:
        selected=state.get('_turn_choice_resolution') or {}
        if selected.get('type')=='visit_day' and selected.get('status')=='resolved':
            expected=selected['choice']['date']
            requested=kwargs.get('date_str', args[0] if args else None)
            if requested not in (None,expected):
                return {'ok':False,'error_code':'CHOICE_DATE_MISMATCH',
                        'error':'The buyer selected another date.',
                        'next_action':'Check the selected date '+expected}
            kwargs['date_str']=expected
            args=()
        result=previous_slots(state,*args,**kwargs)
        menu=choices.choice_from_slots(result) if isinstance(result,dict) else None
        if menu and state.get('_turn_intent') in (
                'visit_planning','visit_day_selection','visit_time_selection'):
            state['_choice_source_this_turn']=menu
        return result

    domain.slots = slots

    # This wrapper runs after main._begin_turn but *before* the Hermes LLM,
    # with the same occupied-by-advisor snapshot used for subsequent tool calls.
    # Without this prefetch, an agent that misses the tool can lose the day.
    previous_chat = hermes_runtime.chat

    @functools.wraps(previous_chat)
    def chat(message: str, history: list[dict], state: dict, **kwargs: Any) -> dict:
        selected=state.get('_turn_choice_resolution') or {}
        if selected.get('type')=='visit_day' and selected.get('status')=='resolved' and not state.get('_choice_failure_this_turn'):
            state['_choice_date_tool_result_this_turn']=domain.slots(
                state,date_str=selected['choice']['date'],
                extra_occupied=kwargs.get('extra_occupied'))
        # Add the typed selection to the already-verified context Hermes reads;
        # do not rewrite the original persisted buyer message/history.
        focus=state.get('conversation_focus')
        if selected and selected.get('status')=='resolved':
            focus=dict(focus or {})
            focus['choice_selection']={
                'type':selected.get('type'),
                'unit_id':selected.get('unit_id'),
                'label':(selected.get('choice') or {}).get('label'),
                'date':(selected.get('choice') or {}).get('date'),
                'time':(selected.get('choice') or {}).get('start_time'),
                'kind':(selected.get('choice') or {}).get('kind'),
            }
            state['conversation_focus']=focus
        try:
            return previous_chat(message,history,state,**kwargs)
        finally:
            if selected and isinstance(state.get('conversation_focus'),dict):
                state['conversation_focus'].pop('choice_selection',None)

    hermes_runtime.chat = chat

    previous_validate = delivery_policy.validate_outbound

    @functools.wraps(previous_validate)
    def validate_outbound(reply: str, state: dict) -> str:
        selected=state.get('_turn_choice_resolution') or {}
        kind=selected.get('type')
        if state.get('_choice_failure_this_turn'):
            if kind in ('visit_day','visit_time'):
                return ('That visit option is no longer available for the selected house. '
                        'Would you like me to check other available dates or times?')
            return 'Could you specify which option you prefer?'
        if kind=='visit_day':
            checked=state.get('_choice_date_tool_result_this_turn')
            if checked is not None and not checked.get('ok'):
                code=checked.get('error_code')
                house=selected.get('unit_id') or 'the selected house'
                day=(selected.get('choice') or {}).get('date')
                if code=='NO_SLOTS':
                    return f'No visit times are available for {house} on {day}. Would you like another day?'
                return f'I could not verify the visit times for {house} on {day}. Would you like me to check another date?'
        if kind=='visit_time' and (state.get('_choice_time_result_this_turn') or {}).get('ok'):
            # An hour was selected, but the only next action is a separately
            # confirmed appointment. The normal end_turn creates confirmation_request.
            return delivery_policy._pending_visit_recap(state)
        if kind=='media' and selected.get('status')=='resolved':
            code=selected.get('unit_id')
            media=domain.unit_media(code)
            if not media.get('error'):
                picked=selected['choice']
                if picked.get('kind')=='gallery' and media.get('gallery_url'):
                    url=media['gallery_url']
                    state.setdefault('_approved_media_urls_this_turn',[]).append(url)
                    return previous_validate(f'Here are the photos for {code}:\n\nView gallery: {url}',state)
                if picked.get('kind')=='documents' and media.get('documents'):
                    docs=media['documents']
                    doc=next((x for x in docs if x.get('type')=='fact_sheet'),docs[0])
                    state.setdefault('_approved_media_urls_this_turn',[]).append(doc['url'])
                    return previous_validate(f'Here is the selected PDF for {code}:\n\n{doc.get("title","PDF")}: {doc["url"]}',state)
            return 'The requested material is not available for this house right now.'
        # Guard the choice protocol independently of generated model prose:
        # only verified dates/hours may become selectable appointment options.
        menu=state.get('_choice_source_this_turn')
        if menu and menu.get('type') in ('visit_day','visit_time'):
            return choices.render_menu(menu)
        answer=previous_validate(reply,state)
        if state.get('_turn_intent') in ('property_selection','property_search','preference_update'):
            # The v13.3.2 catalogue renderer already numbers actual property
            # lists and has regression-tested single-match wording. Do not
            # reinterpret its commercial next-step question as another menu.
            return answer
        resolution=state.get('_turn_property_resolution') or {}
        code=resolution.get('unit_id') or (state.get('conversation_focus') or {}).get('unit_id')
        if code in domain._units_by_code():
            media=domain.unit_media(code)
            if not media.get('error'):
                mm=choices.media_menu(answer,unit_id=code,media=media)
                if mm:
                    state['_choice_source_this_turn']=mm
                    return choices.render_menu(mm)
        generic=choices.generic_menu(answer) or choices.inline_choice_menu(answer)
        if generic:
            state['_choice_source_this_turn']=generic
            return choices.render_menu(generic)
        return answer

    delivery_policy.validate_outbound = validate_outbound

    previous_end = domain.end_turn

    @functools.wraps(previous_end)
    def end_turn(state: dict, reply: str) -> None:
        was_search=state.get('_turn_intent') in ('property_search','preference_update')
        selected_kind=(state.get('_turn_choice_resolution') or {}).get('type')
        menu=state.get('_choice_source_this_turn')
        if menu and menu.get('type') != 'property' and reply != choices.render_menu(menu):
            menu=None  # A tool call is NOT proof that options were delivered.
        previous_end(state,reply)
        if selected_kind in ('visit_time','media','generic') and not menu:
            # A consumed list is no longer the active referent.
            state['last_presented_choice_set']=None
            state['choice_set_initialized']=True
        if was_search:
            property_snapshot=state.get('last_presented_property_options') or {}
            if property_snapshot.get('options') and menu is None:
                options=[{'index':x['display_number'], 'unit_id':x['unit_id'],
                          'label':x.get('commercial_name') or x['unit_id']}
                         for x in property_snapshot['options']]
                menu={'type':'property','options':options,'source_tool':'search_properties'}
            # If a search offered explicit next actions, that is the newest
            # selectable list, not an old property shortlist.
            # A new search with zero matches invalidates earlier ordinal menus.
            state['last_presented_choice_set']=None
            state['choice_set_initialized']=True
        if menu and menu.get('options'):
            reply_id=state['bp_episode']['sales_messages'][-1]['id']
            record=choices.menu_record(menu,reply_message_id=reply_id)
            state['last_presented_choice_set']=record
            state['choice_set_initialized']=True
            history=state.setdefault('choice_set_history',[])
            history.append(copy.deepcopy(record))
            state['choice_set_history']=history[-8:]
            dialogue=state['bp_episode']['sales_dialogue_states']['state_json']
            dialogue['last_presented_choice_set']=copy.deepcopy(record)
        state.pop('_turn_choice_resolution',None)
        state.pop('_selected_choice_this_turn',None)
        state.pop('_choice_source_this_turn',None)
        state.pop('_choice_date_tool_result_this_turn',None)
        state.pop('_choice_time_result_this_turn',None)
        state.pop('_choice_failure_this_turn',None)

    domain.end_turn = end_turn

    previous_profile = domain.profile_snapshot

    @functools.wraps(previous_profile)
    def profile_snapshot(state: dict) -> dict:
        result=previous_profile(state)
        result['last_presented_choice_set']=copy.deepcopy(state.get('last_presented_choice_set'))
        result['choice_set_history']=copy.deepcopy((state.get('choice_set_history') or [])[-3:])
        return result

    domain.profile_snapshot = profile_snapshot

    hermes_runtime.PROMPT += '''\n\nTYPED CHOICE SETS (v13.3.3):
- When presenting TWO OR MORE options the buyer may choose, number them 1., 2.,
  3. on SEPARATE lines. Never number ordinary factual feature lists as choices.
  For one selectable item, do not add artificial numbering.
- A buyer saying "2", "option 2", "the second option", or "la segunda" picks
  option #2 from the most relevant LAST DELIVERED CHOICE SET, not necessarily a
  house. Property, visit day, visit time, and media selections are different.
- The verified context's conversation_focus.choice_selection identifies the
  current resolved selection. Respect its type and value. When it is a DAY,
  check that exact date for the already selected visit house and show verified
  appointment hours. When it is a TIME, recap the exact visit but do not book
  without a distinct affirmative response. When it is MEDIA, share only the
  selected approved resource; never invent a URL.
- Never treat selecting a DAY or TIME as selecting a PROPERTY, and never ask
  for a property already present in visit_draft.unit_id. Explicit corrections
  such as "I meant November 24" override an earlier ordinal misunderstanding.
- Source of truth: calendar for dates/times, catalog for houses, delivered
  message/order for ordinals. A generic option never grants booking consent.
'''
    _INSTALLED = True
