"""Guardrails for a sale-by-SMS Hermes agent, after tools and before DB commit.

Hermes still chooses the conversation and invokes the tools. The backend only
checks outbound media/length against the authoritative catalog tool results.
Never call booking tools again to repair a message.
"""
from __future__ import annotations

import re
from . import sms

_URL = re.compile(r'https?://[^\s<>]+', re.IGNORECASE)
_RELATIVE = re.compile(r'(?<![\w/])/(?:properties|demo-assets)/[^\s)<>]+', re.IGNORECASE)
_INTRO = re.compile(r"^\s*(?:hi[ ,]+[\w'-]+[,.!]?\s*)?i['’]m the ai sales agent assigned to help you[.!]?\s*", re.IGNORECASE)


def _urls(text: str) -> list[str]:
    # Remove punctuation commonly adjacent to URLs, never alter URL interior.
    found = [m.group(0).rstrip('.,;!?)') for m in _URL.finditer(text)]
    found.extend(m.group(0).rstrip('.,;!?)') for m in _RELATIVE.finditer(text))
    return found


def _matching_reply(matches: list[dict], alternatives: list[dict] | None = None) -> str:
    """Deterministic compact fallback: never call a soft mismatch a match."""
    alternatives=alternatives or []
    def price(u: dict) -> str:
        n=int(u['price_usd'])
        return '$'+(f'{n//1000}k' if n%1000==0 else f'{n:,}')
    if not matches:
        if alternatives:
            alt=alternatives[0]
            return (f'No exact match. Optional alternative: {alt["unit_id"]} '
                    f'({alt["bedrooms"]} beds, {price(alt)}), which differs from your '
                    'bedroom preference. Would you like to explore it?')
        return 'No houses meet every preference right now. Which preference would you like to adjust?'
    for count in range(min(len(matches),3),0,-1):
        choices=', '.join(
            f'{u["unit_id"]} ({u["bedrooms"]} beds, {price(u)})' for u in matches[:count])
        candidate=f'I found {len(matches)} matching house(s): {choices}. Which would you like to explore?'
        if sms.details(candidate)['segments']==1:
            return candidate
    return f'I found {len(matches)} matching house(s). Which one would you like to explore?'


def _pending_visit_recap(state: dict) -> str:
    draft=state.get('visit_draft') or {}
    name=draft.get('unit_id') or 'the selected house'
    day=draft.get('date') or 'the selected day'
    clock=draft.get('start_time') or 'the selected hour'
    try:
        hour=int(clock[:2])
        formatted=f'{hour%12 or 12}:{clock[3:5]} {"PM" if hour>=12 else "AM"}'
    except (ValueError,TypeError):
        formatted=clock
    return (f'For {name}, you selected {day} at {formatted} (Lima). '
            'It is not booked yet. Can you reply "Yes, I confirm" to reserve the visit?')


def _record_sent_media(state: dict, message: str) -> None:
    from . import domain
    approved=set(state.get('_approved_media_urls_this_turn') or [])
    sent=approved.intersection(_urls(message))
    if not sent:
        return
    for unit in domain.read_catalog()['properties']:
        media=unit['media']
        gallery_paths=[u for u in sent if media.get('gallery_url') and
                       u.endswith(media['gallery_url'])]
        document_paths={d['url'] for d in media.get('documents',[])}
        sent_docs={u for u in sent if any(u.endswith(path) for path in document_paths)}
        if not gallery_paths and not sent_docs:
            continue
        entry=state.setdefault('shared_media',{}).setdefault(
            unit['unit_id'],{'gallery_sent':False,'document_urls_sent':[]})
        if gallery_paths:entry['gallery_sent']=True
        saved=set(entry.get('document_urls_sent') or [])
        saved.update(sent_docs)
        entry['document_urls_sent']=sorted(saved)

def validate_outbound(reply: str, state: dict) -> str:
    """Avoid duplicate intros, unsolicited/fictional URLs and catalog dumps.

    When Hermes outputs a noncompliant search reply, use the trusted search
    result as a bounded deterministic fallback, never a guessed URL or price.
    For other turns fail closed (the turn isn't committed) rather than sending
    an invalid, potentially misleading link to a lead.
    """
    plain = sms.normalize(reply)
    plain = _INTRO.sub('', plain).strip()
    if not plain:
        raise ValueError('The agent produced only a redundant introduction.')
    allowed = set(state.get('_approved_media_urls_this_turn') or [])
    unauthorised = [url for url in _urls(plain) if url not in allowed]
    search_results = state.get('_matching_options_this_turn')
    if unauthorised:
        if search_results is not None:
            return _matching_reply(search_results)
        # Do not turn a bad link into a plausible but unverifiable address.
        raise ValueError('Hermes returned a URL not authorized by the domain media tool.')
    # The language model cannot promote a selected hour to a confirmed visit.
    draft=state.get('visit_draft') or {}
    active_user_text=str(state.get('_active_source_message_text') or '')
    if state.get('_booking_completion_error'):
        return ('Your requested visit could not be confirmed: ' +
                state['_booking_completion_error'] +
                ' Your existing appointment, if any, is unchanged. Can I check another time?')
    if draft.get('status')=='awaiting_confirmation':
        false_claim=bool(re.search(
            r"\b(?:is confirmed|has been confirmed|i(?:'ve| have) (?:booked|scheduled)|"
            r"your visit is booked|your booking is confirmed)\b",plain,re.I))
        dates=re.findall(r'\b20\d{2}-\d{2}-\d{2}\b',plain)
        wrong_day=bool(dates and any(d!=draft.get('date') for d in dates))
        just_selected=bool(re.search(r'\b\d{1,2}(?::00)?\s*(?:am|pm)\b',active_user_text,re.I))
        if false_claim or wrong_day or just_selected:
            return _pending_visit_recap(state)
    if search_results is not None:
        names={x.upper() for x in re.findall(r'\bH-\d+\b',plain,re.I)}
        matches={u['unit_id'] for u in search_results}
        alternatives=set(state.get('_allowed_alternative_units_this_turn') or [])
        if (not names.issubset(matches|alternatives) or
                sms.details(plain)['segments']>2 or
                (bool(names.intersection(alternatives)) and not re.search(
                    r'\b(?:optional|alternative|different|extra)\b',plain,re.I))):
            return _matching_reply(search_results)
    _record_sent_media(state,plain)
    return plain
