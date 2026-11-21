"""Commercial turn signals: conservative guards, not a competing chatbot.

Hermes interprets complex requests and invokes tools. This module only marks
high-confidence boundaries needed to prevent a stale appointment/property fact
from overriding a fresh search or changing durable preferences.
"""
from __future__ import annotations

import re

_WORD_COUNTS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6}
_COUNT_RE=r'(?P<count>\d{1,2}|one|two|three|four|five|six)\s*(?:[- ]\s*)?(?:bedrooms?|beds?)\b'
_SEARCH=re.compile(
    r'\b(?:other|another|alternatives?|options?|looking for|search|find|'
    r'show me|show(?:ing)? (?:me )?(?:some|more)|have (?:any|other)|'
    r'anything (?:else|cheaper|larger)|'
    r'(?:more|less) expensive|cheaper|larger|bigger|smaller|'
    r'different (?:homes?|houses?|properties)|'
    r'under my budget|within my budget|'
    r'which (?:houses|homes|properties)|compare)\b',
    re.I)
_REVISE=re.compile(
    r'\b(?:actually|from now on|instead|i changed my mind|my new|'
    r'i now (?:want|need|prefer)|now i (?:want|need|prefer)|'
    r'i no longer|no longer want|'
    r'my (?:maximum|max|minimum|min|budget) (?:is|will be)|'
    r'i (?:can|cannot|can.t) (?:go up to|spend more than|spend)|'
    r'i (?:have|need) (?:a )?(?:maximum|minimum|budget) of)\b',
    re.I)
_BOOKING=re.compile(
    r'\b(?:confirm (?:the|my|our) (?:booking|visit|appointment)|'
    r'i confirm|i accept|book(?:ing)? the (?:visit|appointment)|'
    r'cancel (?:my|the) (?:booking|visit)|reschedule|change my (?:visit|appointment))\b', re.I)
_SLOTS=re.compile(
    r'\b(?:visit|tour|appointment|schedule|available slots?|'
    r'(?:next|this) (?:week|weekend)|what (?:days?|times?)|'
    r'which (?:days?|times?)|tomorrow|at \d{1,2}\s*(?:am|pm))\b',re.I)
_ASSETS=re.compile(r'\b(?:photos?|pictures?|gallery|images?|pdfs?|brochures?|fact.?sheets?|floor.?plans?)\b',re.I)


def counts(message: str) -> list[int]:
    text=str(message or '')
    return [int(v) if v.isdigit() else _WORD_COUNTS[v.lower()]
            for m in re.finditer(_COUNT_RE,text,re.I)
            for v in [m.group('count')]]


def search_request(message: str) -> bool:
    """Recognize only a genuinely exploratory ask, never an isolated fact."""
    text=str(message or '')
    if _BOOKING.search(text) and not _SEARCH.search(text):
        return False
    if _SEARCH.search(text):
        # "Show me photos / details of this house" is a factual media/detail
        # request; the command "show me" alone does NOT make it a search.
        is_media=bool(re.search(r'\b(?:photos?|pictures?|gallery|images?|pdf|brochure|floor.?plans?)\b',text,re.I))
        is_detail=bool(re.search(r'\b(?:details|features|characteristics|specifications)\b',text,re.I))
        new_options=bool(re.search(
            r'\b(?:other|another|alternatives?|different (?:homes?|houses?|properties)|'
            r'options? (?:with|under|of)|houses? (?:with|under)|'
            r'properties (?:with|under)|cheaper|more expensive)\b',text,re.I))
        if (is_media or is_detail) and not new_options:
            return False
        if re.search(r'\b(?:financing|finance|mortgage|loan)\s+options?\b',text,re.I) and not new_options:
            return False
        # "What's the price of H-201?" is a fact, not a search.
        if re.search(r'\b(?:what(?:.s| is) the price|how much is)\s+(?:of )?H-\d{3}',text,re.I):
            return False
        return True
    return bool(re.search(
        r'\b(?:houses?|homes?|properties)\b.*\b(?:with|under|below|at most|above|at least)\b.*'
        r'\b(?:bedrooms?|bathrooms?|budget|\$|\d+k)\b',text,re.I))


def clear_permanent_revision(message: str) -> bool:
    text=str(message or '')
    return bool(_REVISE.search(text))


def scope(message: str, has_primary: bool) -> str:
    """Default to temporary comparison when preferences already exist.

    Initial buyer declarations can establish durable preferences. Only an
    explicit revision replaces an established criterion; Hermes may propose
    permanence but the backend insists on supporting buyer evidence.
    """
    if clear_permanent_revision(message):
        return 'permanent'
    if search_request(message) and has_primary:
        return 'temporary'
    # Unambiguous direct declarations about the buyer's own budget are not
    # a comparison, even if a bedroom preference was captured earlier.
    if re.search(
            r"\b(?:i (?:do not|don't|dont) want to spend more than|"
            r"my budget is|my budget (?:will be|should be)|"
            r"my (?:maximum|max|minimum|min) (?:budget )?is|"
            r"i (?:only )?have (?:exactly )?\$?|"
            r"i can (?:afford|spend|pay)|"
            r"my spending limit is)\b",message,re.I) and re.search(
                r"\$|\b\d[\d,]*(?:k|m|000)\b",message,re.I):
        return 'permanent'
    return 'permanent' if not has_primary else 'temporary'


def intent(message: str) -> str:
    text=str(message or '')
    if search_request(text):
        return 'property_search'
    if _BOOKING.search(text):
        return 'booking_action'
    if _ASSETS.search(text):
        return 'property_assets'
    if _SLOTS.search(text):
        return 'visit_planning'
    return 'property_details'


def extracted_criteria(message: str) -> dict:
    """Minimal supported fallback. Hermes can supply more typed filters."""
    c={}
    parsed_counts=counts(message)
    if len(parsed_counts)==1:
        c['bedrooms']=parsed_counts[0]
    elif len(parsed_counts)>1:
        if len(set(parsed_counts))==1:
            c['bedrooms']=parsed_counts[0]
        else:
            c['bedrooms_any']=sorted(set(parsed_counts))
    lower=str(message or '').lower()
    maximum=re.search(r'\b(?:under|up to|below|maximum|max|at most|less than)\s*\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(k|m)?\b',lower)
    if maximum:
        value=float(maximum.group(1).replace(',',''))*(1000 if maximum.group(2)=='k' else 1_000_000 if maximum.group(2)=='m' else 1)
        c['max_price_usd']=int(value)
    minimum=re.search(r'\b(?:above|over|at least|minimum|min)\s*\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(k|m)?\b',lower)
    if minimum:c['min_price_usd']=int(float(minimum.group(1).replace(',',''))*(1000 if minimum.group(2)=='k' else 1_000_000 if minimum.group(2)=='m' else 1))
    return c


def core_query_topic(message: str) -> str | None:
    """Used only for a narrow public factual-read prefetch, not intent."""
    from . import conversation_context
    if search_request(message):
        return None
    return conversation_context.topic(message)
