"""Verified buyer-visible property choices for Hermes Agent Lab.

This module is deliberately presentation/reference logic, not another agent.
Only a response actually delivered with catalog-backed matches creates a
numbered choice list. Choosing a number never authorizes a visit or changes
permanent search preferences.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

_CODE = re.compile(r'(?<![A-Z0-9])H[- ]?(\d{3})(?![A-Z0-9])', re.I)
_NUMBER = re.compile(r'\b(?:option|choice|number|no\.?|#)\s*#?\s*([1-9])\b', re.I)
_ORDINAL = (
    (1, re.compile(r'\b(?:first|1st)(?:\s+(?:one|house|home|property|option))?\b', re.I)),
    (2, re.compile(r'\b(?:second|2nd)(?:\s+(?:one|house|home|property|option))?\b', re.I)),
    (3, re.compile(r'\b(?:third|3rd)(?:\s+(?:one|house|home|property|option))?\b', re.I)),
    (4, re.compile(r'\b(?:fourth|4th)(?:\s+(?:one|house|home|property|option))?\b', re.I)),
)
_PRICE_UNIT = re.compile(r'\$\s*(\d{2,7}(?:,\d{3})*(?:\.\d+)?)\s*([km])?\b', re.I)
_TECHNICAL = re.compile(r'\b(?:system error|validation error|tool error|internal error|unavailable advisor)\b', re.I)
_NEW_SEARCH = re.compile(
    r'\b(?:other|more|different|another)\s+(?:options?|houses?|homes?|properties)\b|'
    r'\b(?:search|find|show me)\s+(?:other|more|different)\s+(?:houses?|homes?|properties)\b', re.I)


def _option_number(message: str) -> int | None:
    match = _NUMBER.search(message or '') or re.search(r'(?<!\w)#\s*([1-9])\b', message or '')
    if match:
        return int(match.group(1))
    for number, expression in _ORDINAL:
        if expression.search(message or ''):
            return number
    if re.search(r'\b(?:the\s+)?latter\b', message or '', re.I):
        return -1
    if re.search(r'\b(?:the\s+)?former\b', message or '', re.I):
        return 1
    return None


def is_option_selection(message: str) -> bool:
    """A numeric/ordinal choice, not a request for *more* properties.

    The surrounding selection language must be recognized before the broad
    `option` keyword in the legacy search parser. An isolated bedroom count
    is not an ordinal property choice.
    """
    text = str(message or '')
    if _NEW_SEARCH.search(text) and not re.search(r'\b(?:option|choice|number)\s*[#:]?\s*[1-9]\b', text, re.I):
        return False
    if _option_number(text) is not None:
        return True
    # "I prefer H-101" / "Tell me about H-101" chooses a displayed unit;
    # "I want to VISIT H-101" must still reach scheduling instead.
    if (_CODE.search(text) and not re.search(
            r'\b(?:visit|tour|book|schedule|appointment|gallery|photos?|pdf|brochure|plans?)\b',
            text,re.I) and re.search(
            r'\b(?:choose|pick|prefer|interested in|details? (?:about|of|on)|'
            r'tell me about|look at|explore)\b',text,re.I)):
        return True
    # Price-specific references in a *choice* (not a budget revision).
    return bool(_PRICE_UNIT.search(text) and re.search(
        r'\b(?:the|that|this|one|house|home|property|prefer|interested|choose|pick)\b',
        text, re.I) and not re.search(
        r'\b(?:budget|spend|maximum|minimum|under|up to|afford|price range)\b', text, re.I))


def _codes(message: str) -> list[str]:
    return list(dict.fromkeys('H-'+m.group(1) for m in _CODE.finditer(message or '')))


def _amount(text: str) -> int | None:
    match = _PRICE_UNIT.search(text or '')
    if not match:
        return None
    num = float(match.group(1).replace(',', ''))
    scale = {'k': 1000, 'm': 1000000}.get((match.group(2) or '').lower(), 1)
    return int(num*scale)


def resolve_selection(state: dict, message: str) -> dict[str, Any] | None:
    """Resolve choices ONLY against the ordered, previously delivered list.

    An unrecognizable, out-of-range or missing snapshot is ambiguous; NEVER
    fall back to the persistent 2-bedroom shortlist for "option 2".
    Explicit unit codes outside the displayed list are resolved by the
    standard conversation resolver, not by this ordinal mapping.
    """
    if not is_option_selection(message):
        return None
    snapshot = state.get('last_presented_property_options') or {}
    options = snapshot.get('options') or []
    if not options:
        return {'unit_id': None, 'ambiguous': True,
                'reason': 'no_verified_presented_options', 'candidates': []}
    explicit = _codes(message)
    if len(explicit) == 1:
        selected = next((o for o in options if o['unit_id']==explicit[0]), None)
        if selected:
            return {'unit_id': selected['unit_id'], 'ambiguous': False,
                    'reason': 'presented_option', 'display_number': selected['display_number'],
                    'source_reply_message_id': snapshot.get('source_reply_message_id')}
        return None
    if len(explicit) > 1:
        return {'unit_id': None, 'ambiguous': True,
                'reason': 'multiple_explicit_options', 'candidates': explicit}
    number = _option_number(message)
    if number == -1:
        number = len(options) if len(options)==2 else None
    if number is not None:
        matches = [o for o in options if o['display_number'] == number]
    else:
        price = _amount(message)
        matches = [o for o in options if o.get('price_usd')==price] if price else []
    if len(matches)==1:
        selected = matches[0]
        return {'unit_id': selected['unit_id'], 'ambiguous': False,
                'reason': 'presented_option', 'display_number': selected['display_number'],
                'source_reply_message_id': snapshot.get('source_reply_message_id')}
    return {'unit_id': None, 'ambiguous': True,
            'reason': 'unknown_or_ambiguous_option',
            'candidates': [o['unit_id'] for o in options]}


def _usd(amount: int | float) -> str:
    return f'${int(amount):,}'


def _available(raw: str | None) -> str:
    if not raw or str(raw).lower() in ('immediate', 'ready now'):
        return 'Ready now'
    try:
        parsed = date.fromisoformat(str(raw))
        return f'Available {parsed.strftime("%b")} {parsed.day}, {parsed.year}'
    except ValueError:
        return f'Availability: {raw}'


def _visible_matches(matches: list[dict]) -> list[dict]:
    return [u for u in matches[:3] if u.get('unit_id')]


def render_matches(matches: list[dict]) -> str:
    """Compact readable plain-text/SMS options with stable numbering.

    This format is intentionally SMS-friendly: no Markdown tables or hidden
    links. All fields come from the approved catalog result.
    """
    visible = _visible_matches(matches)
    if not visible:
        return 'No houses match those criteria. Would you like to adjust a preference?'
    bedrooms = {u.get('bedrooms') for u in visible}
    beds_label = f'{next(iter(bedrooms))}-bedroom ' if len(bedrooms)==1 else ''
    if len(visible)==1:
        u = visible[0]
        return (f'I found a {beds_label}house matching your search:\n\n'
                f'{u["unit_id"]} - {u["commercial_name"]}\n'
                f'{_usd(u["price_usd"])} | {_available(u.get("available_from_date"))}\n\n'
                f'Would you like details about {u["unit_id"]}, or explore homes with different features?')
    lines = [f'I found {len(matches)} {beds_label}houses. Here are the options:', '']
    for i, unit in enumerate(visible, start=1):
        lines.extend([
            f'{i}. {unit["unit_id"]} - {unit["commercial_name"]}',
            f'   {_usd(unit["price_usd"])} | {_available(unit.get("available_from_date"))}',
            '',
        ])
    if len(matches)>len(visible):
        lines.append(f'{len(matches)-len(visible)} more matching properties are available.')
        lines.append('')
    choices=('option 1 or 2' if len(visible)==2 else
             'option 1, 2 or 3' if len(visible)==3 else 'one of these options')
    lines.append(f'Would you like details about {choices}, or explore homes with different features?')
    return '\n'.join(lines)


def needs_listing_repair(reply: str, matches: list[dict]) -> bool:
    """A formatted result must be faithful, ordered, and readable."""
    if not matches:
        return False
    visible = _visible_matches(matches)
    codes = _codes(reply)
    expected = [u['unit_id'] for u in visible]
    if codes != expected or _TECHNICAL.search(reply):
        return True
    if len(visible)==1:
        return '\n' not in reply or bool(re.search(r'(?m)^\s*1\.\s', reply)) or bool(re.search(
            r'\bwhich (?:one|house)\b', reply, re.I))
    if '\n' not in reply:
        return True
    # Never let two menu labels collapse into one run-on paragraph.
    positions=[]
    for i, code in enumerate(expected, 1):
        m=re.search(rf'(?m)^\s*{i}\.\s*{re.escape(code)}\b',reply,re.I)
        if not m:
            return True
        positions.append(m.start())
    return positions!=sorted(positions)


def delivered_snapshot(matches: list[dict], reply: str, *,
                       search_revision: int | None = None,
                       reply_message_id: str | None = None) -> dict | None:
    """Only persist items actually *visible* in the outgoing buyer reply."""
    visible = _visible_matches(matches)
    if not visible:
        return None
    order=[]
    for unit in visible:
        matches_in_reply = list(re.finditer(r'(?<![\w])'+re.escape(unit['unit_id'])+r'\b',reply,re.I))
        if matches_in_reply:
            order.append((matches_in_reply[0].start(),unit))
    if not order:
        return None
    order.sort(key=lambda p:p[0])
    if len(order)!=len(visible):
        return None  # No hidden/unpresented units in the ordinal mapping.
    return {'search_revision':search_revision,
            'source_reply_message_id':reply_message_id,
            'options':[{'display_number':i,'unit_id':u['unit_id'],
                        'commercial_name':u.get('commercial_name'),
                        'price_usd':u.get('price_usd')}
                       for i,(_,u) in enumerate(order,1)]}


def selected_property_reply(unit: dict) -> str:
    """Safe fact-based recovery if Hermes ignores a selected menu option."""
    features = unit.get('confirmed_features') or {}
    facts=[f'{unit["bedrooms"]} bedrooms', f'{unit["bathrooms"]} bathrooms',
           f'{unit["area_sqm"]} sq m of built area']
    if features.get('kitchen_layout'):
        facts.append(f'{features["kitchen_layout"]} kitchen')
    parking=features.get('parking_spaces')
    if parking is not None:
        facts.append(f'{parking} parking space'+('s' if int(parking)!=1 else ''))
    answer=(f'Great choice to explore: {unit["commercial_name"]} ({unit["unit_id"]}).\n\n'
            + ', '.join(facts)+'.\n'
            + f'Price: {_usd(unit["price_usd"])} | {_available(unit.get("available_from_date"))}.\n\n'
            + 'Would you like photos, the available property PDF, or to plan a visit?')
    return answer


def needs_selection_repair(reply: str, unit: dict) -> bool:
    text=str(reply or '')
    if _TECHNICAL.search(text) or re.search(
            r'\b(?:found\s+\d+\s+matching|which\s+(?:one|house)\s+would you|'
            r'looking for a different|please select a property)\b',text,re.I):
        return True
    mentioned=_codes(text)
    if mentioned and any(c!=unit['unit_id'] for c in mentioned):
        return True
    if unit['unit_id'] not in mentioned and unit['commercial_name'].lower() not in text.lower():
        return True
    return False
