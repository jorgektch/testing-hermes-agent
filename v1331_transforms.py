"""Anchor-checked integration transforms for *actual* production v13.3.

The earlier packaging installer expected v13.2 imports that are absent in the
v13.3 production commit. These transforms match the reviewed commit exactly.
"""
from __future__ import annotations
from installer_core import Edit, PatchError


def commercial(source: str) -> str:
    e=Edit('backend/app/commercial_turn.py',source)
    e.insert_after('import re\n','\nfrom . import property_options\n')
    e.function_body_insert('search_request',"    text=str(message or '')\n",
        "    if property_options.is_option_selection(text):\n        return False\n")
    e.replace('def intent(message: str) -> str:',
              'def intent(message: str, state: dict | None = None) -> str:')
    e.function_body_insert('intent',"    text=str(message or '')\n", """    state=state or {}
    if property_options.is_option_selection(
            text, has_visible_options=bool(
                (state.get('last_presented_property_options') or {}).get('options'))):
        return 'property_selection'
""")
    return e.text


def context(source: str) -> str:
    e=Edit('backend/app/conversation_context.py',source)
    e.insert_after('from typing import Any\n','\nfrom . import property_options\n')
    e.replace("    recent=[c for c in (state.get('last_presented_units') or []) if c in units]", """    if property_options.is_option_selection(
            text, has_visible_options=bool(
                (state.get('last_presented_property_options') or {}).get('options'))):
        selected=property_options.resolve_selection(state,text)
        if selected is not None:
            return selected
    recent=[c for c in (state.get('last_presented_units') or []) if c in units]""")
    return e.text


def domain(source: str) -> str:
    e=Edit('backend/app/domain.py',source)
    e.insert_after('from . import scheduling, sales_preferences, conversation_context, property_filters, commercial_turn, search_sessions\n',
                   'from . import property_options\n')
    e.replace("        'active_search': None,\n",
              "        'active_search': None,\n        'last_presented_property_options': None,\n")
    e.insert_after("    state['_turn_property_resolution']=resolution\n", """    if state.get('_turn_intent')=='property_selection':
        # The buyer chose a *shown* property for details. No search or booking.
        selected=resolution.get('unit_id')
        if selected in known and not resolution.get('ambiguous'):
            draft=_draft(state)
            if draft.get('unit_id') and draft.get('status') not in ('idle','confirmed'):
                state['visit_planning_paused']=True
                state['confirmation_request']=None
                state['pending_visit_offer']=None
            _set_focus(state,selected)
            state['conversation_phase']='property_details'
            state['stage']='Exploring property details'
            track_property_interest(state,selected,'considering')
            state['_selected_property_option_this_turn']={
                'unit_id':selected,'display_number':resolution.get('display_number'),
                'source_reply_message_id':resolution.get('source_reply_message_id')}
        else:
            state['_turn_property_ambiguous']=True
        return
""")
    e.replace("    new_mentions=conversation_context.presented_units(reply,_units_by_code())\n", """    if (state.get('_turn_intent') in ('property_search','preference_update') and
            state.get('_matching_options_this_turn') is not None):
        options=property_options.delivered_snapshot(
            state['_matching_options_this_turn'],reply,
            search_revision=(state.get('active_search') or {}).get('revision'),
            reply_message_id=bp['sales_messages'][-1]['id'])
        state['last_presented_property_options']=options
        if options:
            state['last_presented_units']=[o['unit_id'] for o in options['options']]
    new_mentions=conversation_context.presented_units(reply,_units_by_code())
""")
    e.replace("    state.pop('_search_tool_called_this_turn', None)\n",
              "    state.pop('_search_tool_called_this_turn', None)\n    state.pop('_selected_property_option_this_turn', None)\n")
    e.function_body_insert('evaluate_properties', "    lead = state.get('lead', {})\n", """    if state.get('_turn_intent')=='property_selection':
        return {'error':'The buyer selected a shown property, not a new search.',
                'error_code':'PROPERTY_SELECTION_NOT_SEARCH',
                'next_action':'Explain the selected public house from get_unit_details.'}
""")
    e.replace("    utterance=str(state.get('_active_source_message_text') or '')\n", """    if state.get('_turn_intent')=='property_selection':
        return {'error':'The buyer selected an existing option, not a new search.',
                'error_code':'PROPERTY_SELECTION_NOT_SEARCH',
                'next_action':'Show details of the resolved unit.'}
    utterance=str(state.get('_active_source_message_text') or '')
""")
    e.function_body_insert('slots', '    known = _units_by_code()\n', """    if state.get('_turn_intent')=='property_selection':
        return _slot_error('VISIT_NOT_REQUESTED',
            'The buyer selected a house for details, not a visit.',
            next_action='Only check visit slots when the buyer asks to visit.')
""")
    e.replace("    if status == 'interested' and state.get('_active_source_message_id'):\n", """    if status == 'interested' and state.get('_turn_intent')=='property_selection':
        return {'error':'Selection for details is not a visit or purchase commitment.',
                'error_code':'INTEREST_LEVEL_NOT_DECLARED',
                'next_action':'The domain has already saved considering status.'}
    if status == 'interested' and state.get('_active_source_message_id'):
""")
    e.insert_after("        'active_search':copy.deepcopy(state.get('active_search')),\n",
                   "        'last_presented_property_options':copy.deepcopy(state.get('last_presented_property_options')),\n")
    e.replace("    state['_turn_intent']=commercial_turn.intent(message)\n",
              "    state['_turn_intent']=commercial_turn.intent(message,state=state)\n")
    return e.text


def policy(source: str) -> str:
    e=Edit('backend/app/delivery_policy.py',source)
    e.insert_after('from . import sms\n','from . import property_options\n')
    start=e.text.find('def _matching_reply(')
    end=e.text.find('\ndef _no_matches_for_state(',start)
    if start<0 or end<0:
        raise PatchError('delivery_policy: matching_reply boundary not found')
    replacement='''def _matching_reply(matches: list[dict], alternatives: list[dict] | None = None) -> str:
    """Catalog-verified SMS-friendly property menu."""
    alternatives=alternatives or []
    if matches:
        return property_options.render_matches(matches)
    if alternatives:
        alt=alternatives[0]
        return (f'No exact match. Optional alternative: {alt["unit_id"]} '
                f'({alt["bedrooms"]} bedrooms, ${int(alt["price_usd"]):,}), '
                'which differs from your preferences. Would you like its details?')
    return 'No houses meet every preference right now. Would you like to adjust a criterion?'

'''
    e.text=e.text[:start]+replacement+e.text[end:]
    e.insert_before('    # The language model cannot promote a selected hour to a confirmed visit.\n', """    if state.get('_turn_intent')=='property_selection':
        from . import domain
        ref=state.get('_turn_property_resolution') or {}
        unit=domain._units_by_code().get(ref.get('unit_id'))
        if unit and not ref.get('ambiguous'):
            if property_options.needs_selection_repair(plain,unit):
                plain=property_options.selected_property_reply(unit)
                state.setdefault('tool_observations',[]).append({
                    'tool':'selected_property_reply_recovery','status':'recovered',
                    'fields':['selected_unit_id'],
                    'message_id':state.get('_active_source_message_id'),
                    'timestamp':domain.now_local().isoformat()})
            _record_sent_media(state,plain)
            return _suggest_available_assets(state,plain)
        return 'Which house would you like to explore? Please use its code, for example H-101.'
""")
    anchor='    _record_sent_media(state,plain)\n'
    pos=e.text.rfind(anchor)
    if pos<0:
        raise PatchError('delivery_policy: final media-record anchor not found')
    e.text=e.text[:pos]+"""    if commercial_search and search_results:
        if property_options.needs_listing_repair(plain,search_results):
            plain=property_options.render_matches(search_results)
"""+e.text[pos:]
    return e.text
