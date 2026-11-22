"""Hermes v13.3.2 Upload Ready: integration with the installed v13.3 API.

A narrow compatibility adapter is used because this delivery is installed via
GitHub's Upload files (not git apply). It integrates the already-present domain,
Hermes, SMS and Turso call paths without requiring a migration or executing an
installer. Hermes still calls its own registered tools; no extractor LLM exists.

Install occurs once during the app package import, before FastAPI imports tools.
"""
from __future__ import annotations

import copy
import functools
import re
from contextvars import ContextVar
from typing import Any

from . import property_options

_ACTIVE_TURN: ContextVar[dict | None] = ContextVar('hermes_1332_active_turn', default=None)
_INSTALLED = False


def _selection(state: dict, message: str) -> bool:
    return property_options.is_option_selection(
        message, has_visible_options=bool((state.get('last_presented_property_options') or {}).get('options')))


def _selected_unit(state: dict, domain: Any) -> dict | None:
    ref = state.get('_turn_property_resolution') or {}
    if ref.get('ambiguous'):
        return None
    return domain._units_by_code().get(ref.get('unit_id'))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # Import the production modules first, then install wrappers. main.py uses
    # their public functions by name, so imported callable references remain live.
    from . import commercial_turn, conversation_context, domain, delivery_policy, sms, hermes_runtime

    old_search_request = commercial_turn.search_request

    @functools.wraps(old_search_request)
    def search_request(message: str) -> bool:
        state = _ACTIVE_TURN.get()
        if state is not None and _selection(state, message):
            return False
        if property_options.is_option_selection(message):
            return False
        return old_search_request(message)

    commercial_turn.search_request = search_request

    old_intent = commercial_turn.intent

    @functools.wraps(old_intent)
    def intent(message: str, state: dict | None = None) -> str:
        live_state = state if state is not None else _ACTIVE_TURN.get()
        if live_state is not None and _selection(live_state, message):
            return 'property_selection'
        if property_options.is_option_selection(message):
            return 'property_selection'
        return old_intent(message)

    commercial_turn.intent = intent

    old_resolver = conversation_context.resolved_reference

    @functools.wraps(old_resolver)
    def resolved_reference(state: dict, text: str, units: dict[str, dict]) -> dict:
        if state.get('_turn_intent') == 'property_selection':
            resolved = property_options.resolve_selection(state, text)
            if resolved is not None:
                return resolved
        return old_resolver(state, text, units)

    conversation_context.resolved_reference = resolved_reference

    old_blank = domain.blank_state

    @functools.wraps(old_blank)
    def blank_state() -> dict:
        state = old_blank()
        state['last_presented_property_options'] = None
        return state

    domain.blank_state = blank_state

    old_begin = domain.begin_turn

    @functools.wraps(old_begin)
    def begin_turn(state: dict, message: str) -> None:
        state.setdefault('last_presented_property_options', None)
        token = _ACTIVE_TURN.set(state)
        try:
            old_begin(state, message)
        finally:
            _ACTIVE_TURN.reset(token)
        if state.get('_turn_intent') != 'property_selection':
            return
        resolution = state.get('_turn_property_resolution') or {}
        code = resolution.get('unit_id')
        if (not code or resolution.get('ambiguous') or
                code not in domain._units_by_code()):
            state['_turn_property_ambiguous'] = True
            return
        # The purchase and appointment flows remain separate. A change of focus
        # invalidates stale confirmation, but never silently loses the draft.
        draft = domain._draft(state)
        if draft.get('unit_id') and draft.get('status') not in ('idle', 'confirmed'):
            state['visit_planning_paused'] = True
            state['confirmation_request'] = None
            state['pending_visit_offer'] = None
        domain._set_focus(state, code)
        state['conversation_phase'] = 'property_details'
        state['stage'] = 'Exploring property details'
        domain.track_property_interest(state, code, 'considering')
        state['_selected_property_option_this_turn'] = {
            'unit_id': code,
            'display_number': resolution.get('display_number'),
            'source_reply_message_id': resolution.get('source_reply_message_id'),
        }

    domain.begin_turn = begin_turn

    old_verify = domain.verify_explicit_preferences

    @functools.wraps(old_verify)
    def verify_explicit_preferences(state: dict, message: str) -> dict:
        if state.get('_turn_intent') == 'property_selection':
            return {'ok': True, 'changed_fields': {},
                    'note': 'The buyer selected a displayed house; preferences unchanged.'}
        return old_verify(state, message)

    domain.verify_explicit_preferences = verify_explicit_preferences

    old_eval = domain.evaluate_properties

    @functools.wraps(old_eval)
    def evaluate_properties(state: dict, *args: Any, **kwargs: Any) -> dict:
        if state.get('_turn_intent') == 'property_selection':
            return {'error_code': 'PROPERTY_SELECTION_NOT_SEARCH',
                    'error': 'A previously offered house was selected for details, not a search.',
                    'next_action': 'Call get_unit_details on the selected property.'}
        return old_eval(state, *args, **kwargs)

    domain.evaluate_properties = evaluate_properties

    old_search = domain.search_properties

    @functools.wraps(old_search)
    def search_properties(state: dict, *args: Any, **kwargs: Any) -> dict:
        if state.get('_turn_intent') == 'property_selection':
            return {'error_code': 'PROPERTY_SELECTION_NOT_SEARCH',
                    'error': 'Option selection is not a new property search.',
                    'next_action': 'Use the resolved property code in get_unit_details.'}
        return old_search(state, *args, **kwargs)

    domain.search_properties = search_properties

    old_slots = domain.slots

    @functools.wraps(old_slots)
    def slots(state: dict, *args: Any, **kwargs: Any) -> dict:
        if state.get('_turn_intent') == 'property_selection':
            return {'ok': False, 'error_code': 'VISIT_NOT_REQUESTED',
                    'error': 'Selecting a property for details is not a visit request.',
                    'next_action': 'Only check slots after the buyer requests a visit.'}
        return old_slots(state, *args, **kwargs)

    domain.slots = slots

    old_interest = domain.track_property_interest

    @functools.wraps(old_interest)
    def track_property_interest(state: dict, unit_id: str, status: str = 'discussed') -> dict:
        if state.get('_turn_intent') == 'property_selection' and status == 'interested':
            return {'error_code': 'INTEREST_LEVEL_NOT_DECLARED',
                    'error': 'A details selection is not a purchase/visit commitment.'}
        return old_interest(state, unit_id, status)

    domain.track_property_interest = track_property_interest

    old_matching_reply = delivery_policy._matching_reply

    @functools.wraps(old_matching_reply)
    def matching_reply(matches: list[dict], alternatives: list[dict] | None = None) -> str:
        if matches:
            return property_options.render_matches(matches)
        return old_matching_reply(matches, alternatives)

    delivery_policy._matching_reply = matching_reply

    old_validate = delivery_policy.validate_outbound

    @functools.wraps(old_validate)
    def validate_outbound(reply: str, state: dict) -> str:
        # The selected option must not be undone by an LLM-generated old search.
        if state.get('_turn_intent') == 'property_selection':
            unit = _selected_unit(state, domain)
            if unit is None:
                return 'Which house would you like to explore? Please use its property code (for example H-101).'
            candidate = sms.normalize(reply)
            if property_options.needs_selection_repair(candidate, unit):
                state.setdefault('tool_observations', []).append({
                    'tool': 'selected_property_reply_recovery', 'status': 'recovered',
                    'fields': ['selected_unit_id'],
                    'message_id': state.get('_active_source_message_id'),
                    'timestamp': domain.now_local().isoformat()})
                return property_options.selected_property_reply(unit)
            try:
                safe = old_validate(candidate, state)
                if property_options.needs_selection_repair(safe, unit):
                    return property_options.selected_property_reply(unit)
                return safe
            except ValueError:
                return property_options.selected_property_reply(unit)
        answer = old_validate(reply, state)
        matches = state.get('_matching_options_this_turn')
        if (state.get('_turn_intent') in ('property_search', 'preference_update')
                and isinstance(matches, list) and matches
                and property_options.needs_listing_repair(answer, matches)):
            return property_options.render_matches(matches)
        return answer

    delivery_policy.validate_outbound = validate_outbound

    old_end = domain.end_turn

    @functools.wraps(old_end)
    def end_turn(state: dict, reply: str) -> None:
        matches = state.get('_matching_options_this_turn')
        was_search = state.get('_turn_intent') in ('property_search', 'preference_update')
        search_revision = (state.get('active_search') or {}).get('revision')
        old_end(state, reply)
        if was_search and isinstance(matches, list):
            message_id = state['bp_episode']['sales_messages'][-1]['id']
            snapshot = property_options.delivered_snapshot(
                matches, reply, search_revision=search_revision,
                reply_message_id=message_id)
            state['last_presented_property_options'] = snapshot
            state['last_presented_units'] = (
                [opt['unit_id'] for opt in snapshot['options']] if snapshot else [])
            state['bp_episode']['sales_dialogue_states']['state_json'][
                'last_presented_property_options'] = copy.deepcopy(snapshot)
        state.pop('_selected_property_option_this_turn', None)

    domain.end_turn = end_turn

    old_profile = domain.profile_snapshot

    @functools.wraps(old_profile)
    def profile_snapshot(state: dict) -> dict:
        result = old_profile(state)
        result['last_presented_property_options'] = copy.deepcopy(
            state.get('last_presented_property_options'))
        return result

    domain.profile_snapshot = profile_snapshot

    old_outgoing = sms.outgoing_segments

    @functools.wraps(old_outgoing)
    def outgoing_segments(text: str) -> list[str]:
        """Keep line structure in each logical SMS and the persisted DB row.

        Physical carrier segmentation is distinct from a conversation bubble.
        One logical message may span multiple carrier segments, which the
        recipient's device reassembles. Avoid flattening the numbered menu.
        """
        plain = sms.normalize(text)
        if not plain:
            raise ValueError('Cannot send an empty SMS.')
        if '\n' in plain:
            return [plain]
        return old_outgoing(plain)

    sms.outgoing_segments = outgoing_segments

    hermes_runtime.PROMPT += '''\n\nBUYER-VISIBLE OPTIONS (DELIVERY CONTRACT):
- Distinguish a buyer selecting a displayed option from requesting a NEW search.
  "The second option", "Option 2", "2", or "I choose the $320k house"
  refer to the last NUMBERED menu that was actually delivered, never an older
  shortlist. The backend has already resolved the chosen unit into the turn
  property reference and focused it in state.
- For a selection, call get_unit_details for the resolved/focused house and
  explain it. Do not call search_properties, check slots, or confirm a visit
  unless the buyer explicitly asks for that. Choosing details is not consent.
- For multiple property results, use one property per line in the actual order
  shown, with a stable ordinal and the exact published code and price; ordinary
  SMS plain text and literal newlines are required (not Markdown tables).
- If a choice is ambiguous, ask for the unit code instead of guessing.
'''
    _INSTALLED = True
