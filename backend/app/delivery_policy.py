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


def _matching_reply(matches: list[dict]) -> str:
    if not matches:
        return 'No houses match every requirement right now. Which preference would you like to adjust?'
    def price(unit: dict) -> str:
        amount = int(unit['price_usd'])
        return f'${amount // 1000}k' if amount % 1000 == 0 else f'${amount:,}'
    for count in (min(len(matches), 3), 2, 1):
        choices = ', '.join(f"{u['unit_id']} ({u['bedrooms']} beds, {price(u)})" for u in matches[:count])
        lead_in = f'I found {len(matches)} matching houses: '
        candidate = f'{lead_in}{choices}. Which one would you like to explore?'
        if sms.details(candidate)['segments'] == 1:
            return candidate
    return f'I found {len(matches)} matching houses. Which would you like to explore first?'


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
    # A matching result should lead to a choice, not a full brochure and links.
    if search_results is not None:
        # Even if the LLM avoids a URL, it may mistakenly recommend a house
        # outside the vetted match list. Never turn those into an offer.
        names = set(re.findall(r'\bH-\d+\b', plain, flags=re.IGNORECASE))
        allowed_units = {u['unit_id'] for u in search_results}
        if (not names.issubset(allowed_units) or
                sms.details(plain)['segments'] > 1):
            return _matching_reply(search_results)
    # Other multi-segment replies are delivered as one coherent SMS message by
    # the existing semantic SMS layer. Avoid a destructive truncation.
    return plain
