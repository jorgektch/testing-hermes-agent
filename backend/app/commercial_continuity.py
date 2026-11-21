"""V13.2: intent-sensitive continuity around a single Hermes sales conversation.

Pure commercial signals. The LLM/Hermes owns dialogue, and verified backend
services own catalog, mutable preferences, booking offers and transactions.
Never treat an assistant-mentioned home as approval to change a visit.
"""
from __future__ import annotations

import re
from typing import Any

CALENDAR_WORDS = re.compile(
    r'\b(?:days?|dates?|week|weeks|weekends?|tomorrow|appointments?|'
    r'visits?|viewings?|slots?|hours?|times?|schedule|availability|'
    r'morning|afternoon|evening)\b', re.I)
PROPERTY_WORDS = re.compile(
    r'\b(?:homes?|houses?|properties|listings?|bedrooms?|bathrooms?|'
    r'garages?|parking|price|prices|budget|square\s*(?:feet|metres|meters)|'
    r'backyards?|kitchens?)\b', re.I)
OTHER_DAYS = re.compile(
    r'\b(?:other|another|following|different|next|which|what|remaining)\s+'
    r'(?:business\s+)?(?:days?|dates?|weeks?|weekends?|hours?|times?|slots?)\b',re.I)
CLOCK = re.compile(r'(?<!\d)(?:(?:0?[1-9]|1[0-2])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|(?:[01]\d|2[0-3]):[0-5]\d)(?!\d)',re.I)
NEW_VISIT = re.compile(
    r'\b(?:i(?:\s+would|\s*\'d)?\s+(?:like|want|need)\s+to\s+'
    r'(?:visit|see|tour|view)|(?:book|schedule|arrange)\s+(?:a\s+)?'
    r'(?:visit|tour|viewing)|(?:visit|tour)\s+(?:that|this|the|h[- ]?\d{3})|'
    r'(?:switch|change|move)\s+(?:my|the)?\s*visit\s+to)\b',re.I)


def is_calendar_question(text: str, *, has_visit: bool = False, phase: str = '') -> bool:
    """Disambiguate 'other DAYS' from 'other HOUSES' before broad search rules.

    Distinguish price/bedroom search from mixed calendar references. A user can
    ask for a new house while a visit is pending by actually naming homes.
    """
    q=str(text or '')
    if OTHER_DAYS.search(q):
        return True
    if re.search(r'\b(?:8|9|10|11|12)(?::00)?\s*(?:am|pm|a\.m\.|p\.m\.)\b',q,re.I) and (
        CALENDAR_WORDS.search(q) or has_visit):
        return True
    if re.search(r'\b(?:when can (?:i|we)|what day|which day|what time|which time|'
                 r'any day|available (?:dates|days|times|slots)|next week)\b',q,re.I):
        if (has_visit or phase=='visit_planning' or re.search(
             r'\b(?:visits?|viewings?|appointments?|schedule|slots?)\b',q,re.I)):
            if not re.search(r'\b(?:other|another)\s+(?:houses?|homes?|properties|listings?)\b',q,re.I):
                return True
    if has_visit and CLOCK.search(q) and not PROPERTY_WORDS.search(q):
        return True
    if has_visit and re.search(r'\b(?:visit|view|viewing|tour|appointment)\b',q,re.I) and re.search(
            r'\b(?:january|february|march|april|may|june|july|august|september|'
            r'october|november|december|20\d{2}-\d{2}-\d{2})\b',q,re.I):
        return True
    return False


def is_new_visit_request(text: str) -> bool:
    return bool(NEW_VISIT.search(str(text or '')))


def is_booking_followup(text: str, *, has_visit: bool = False, phase: str = '') -> bool:
    q=str(text or '')
    if is_calendar_question(q,has_visit=has_visit,phase=phase):
        return True
    if has_visit and (CLOCK.search(q) or re.fullmatch(
            r'\s*(?:yes|ok|okay|sure|that works|sounds good|'
            r'(?:9|10|11)\s*(?:am|a\.m\.))\s*[.!]?\s*',q,re.I)):
        return True
    return False


def explicit_visit_switch(text: str, candidate: str | None, current: str | None) -> bool:
    """Explicit named change only; an offered home never changes a visit."""
    if not candidate or candidate==current:
        return False
    q=str(text or '')
    code=re.escape(candidate)
    names=bool(re.search(r'(?<!\w)'+code.replace(r'\-',r'[- ]?')+r'(?!\w)',q,re.I))
    if not names:
        return False
    return bool(re.search(r'\b(?:visit|tour|view|schedule|book|switch|change|move)\b',q,re.I))


def clock_value(text: str) -> str | None:
    values=set()
    for m in CLOCK.finditer(str(text or '')):
        raw=m.group(0).lower().replace(' ','').replace('.','')
        if raw.endswith(('am','pm')):
            meridian=raw[-2:];hhmm=raw[:-2]
            h,_,minutes=hhmm.partition(':')
            hour=int(h)%12+(12 if meridian=='pm' else 0)
            values.add(f'{hour:02d}:{int(minutes or 0):02d}')
        else:
            values.add(raw)
    return next(iter(values)) if len(values)==1 else None


def property_variances(state: dict, unit: dict) -> dict[str,Any]:
    pref=(state.get('search_preferences') or {})
    beds=(pref.get('bedrooms') or {}).get('preferred')
    cap=(pref.get('budget') or {}).get('maximum_usd')
    diff={}
    if isinstance(beds,int) and unit.get('bedrooms')!=beds:
        diff['bedrooms']={'current':beds,'suggested':unit['bedrooms']}
    if isinstance(cap,int) and isinstance(unit.get('price_usd'),int) and unit['price_usd']>cap:
        diff['budget_maximum_usd']={'current':cap,'suggested':unit['price_usd'],
                                    'excess_usd':unit['price_usd']-cap}
    return diff


def filter_variances(state:dict,unit:dict) -> dict[str,Any]:
    """Differences against durable composable buyer search requirements."""
    filters=state.get('property_filters') or {}
    features=unit.get('confirmed_features') or {}
    values={'min_area_sqm':unit.get('area_sqm'),
            'max_area_sqm':unit.get('area_sqm'),
            'min_bathrooms':unit.get('bathrooms'),
            'max_bathrooms':unit.get('bathrooms'),
            'min_parking_spaces':features.get('parking_spaces'),
            'kitchen_layout':features.get('kitchen_layout'),
            'private_backyard':features.get('private_backyard')}
    outcomes={}
    for key,actual in values.items():
        requested=filters.get(key)
        if requested is None or actual is None:continue
        if key.startswith('min_') and float(actual)>=float(requested):continue
        if key.startswith('max_') and float(actual)<=float(requested):continue
        if key in ('kitchen_layout','private_backyard') and actual==requested:continue
        outcomes[key]={'current':requested,'suggested':actual}
    return outcomes


def register_interest(state:dict, unit:dict, *, source_message_id:str|None,
                      status:str='interested') -> dict:
    """Observable interest in a verified home does not imply a visit or a sale."""
    records=state.setdefault('property_interests',[])
    code=unit['unit_id']
    entry=next((r for r in records if r.get('unit_id')==code),None)
    rank={'discussed':0,'considering':1,'interested':2,'visit_requested':3}
    if entry is None:
        entry={'unit_id':code,'status':status,'source_message_id':source_message_id}
        records.append(entry)
    elif rank.get(status,0)>=rank.get(entry.get('status'),0):
        entry.update(status=status,source_message_id=source_message_id)
    state.setdefault('lead',{})['interest_unit_id']=code
    return entry


def maybe_propose_preference_update(state:dict, unit:dict,
                                    *, source_message_id:str|None) -> dict|None:
    """Offer advice once per property+primary revision; never mutate preferences."""
    changes=property_variances(state,unit)
    filter_changes=filter_variances(state,unit)
    if not changes and not filter_changes:
        return None
    record=state.setdefault('preference_update_proposals',{}).get(unit['unit_id'])
    revision=(state.get('search_preferences') or {}).get('revision',0)
    if record and record.get('primary_revision')==revision:
        return None
    proposal={'unit_id':unit['unit_id'],'primary_revision':revision,
              'suggested_changes':changes,
              'suggested_filter_changes':filter_changes,'status':'pending',
              'source_message_id':source_message_id,'offered_message_id':None}
    state['preference_update_proposals'][unit['unit_id']]=proposal
    return proposal


def preference_offer_text(state:dict, unit:dict) -> str|None:
    offer=(state.get('preference_update_proposals') or {}).get(unit['unit_id'])
    if not offer or offer.get('status')!='pending':
        return None
    changes=offer['suggested_changes']
    distinctions=[]
    if 'budget_maximum_usd' in changes:
        d=changes['budget_maximum_usd']
        distinctions.append(f'${d["excess_usd"]:,} above your ${d["current"]:,} budget')
    if 'bedrooms' in changes:
        d=changes['bedrooms']
        distinctions.append(f'{d["suggested"]} bedrooms rather than your preferred {d["current"]}')
    if offer.get('suggested_filter_changes'):
        named={'min_area_sqm':'minimum area','max_area_sqm':'maximum area',
               'min_bathrooms':'minimum bathrooms','max_bathrooms':'maximum bathrooms',
               'min_parking_spaces':'parking','kitchen_layout':'kitchen',
               'private_backyard':'backyard'}
        distinctions.append('different '+', '.join(
            named.get(k,k) for k in offer['suggested_filter_changes']))
    if not distinctions:return None
    return (f'{unit["unit_id"]} is '+ ' and '.join(distinctions)+'. '
            'Would you like to update those search preferences, or keep this home as an exception? '
            'Either way, we can continue exploring it.')


def preference_offer_decision(state:dict, text:str, units:dict[str,dict]) -> dict:
    """Apply ONLY explicit assent to an already offered, identifiable proposal.

    Requires an earlier response with offered_message_id; a bare OK is not
    agreement. Picking a house or requesting a visit never changes the cap.
    """
    q=str(text or '').strip()
    if not q:return {'action':'none'}
    offers=state.get('preference_update_proposals') or {}
    pending=[o for o in offers.values() if o.get('status')=='offered']
    if not pending:return {'action':'none'}
    named=[o for o in pending if o['unit_id'].lower() in q.lower() or
           o['unit_id'].replace('-','').lower() in q.replace('-','').lower()]
    if len(named)==1:offer=named[0]
    elif len(pending)==1:offer=pending[0]
    else:return {'action':'none'}
    if re.search(r'\b(?:keep (?:my|the) (?:old|original|current)|'
                 r'don.t update|do not update|no changes|'
                 r'one[- ]off|exception only|as an exception)\b',q,re.I):
        offer['status']='exception_only'
        return {'action':'exception_only','unit_id':offer['unit_id']}
    if '?' in q and not re.search(r'^\s*yes[, ]+update\b',q,re.I):
        return {'action':'none'}
    if not re.search(r'\b(?:yes[, ]+update|update (?:both|all|my|the)|'
                 r'change (?:my|the) preferences|raise my budget|'
                 r'change my preferred bedrooms)\b',q,re.I):
        return {'action':'none'}
    changes=offer['suggested_changes']
    both=bool(re.search(r'\b(?:both|all|those|my preferences|the preferences)\b',q,re.I))
    fields=[]
    if ('budget_maximum_usd' in changes and (both or re.search(r'\b(?:budget|price|maximum|cap)\b',q,re.I))):
        fields.append('budget')
    if ('bedrooms' in changes and (both or re.search(r'\b(?:bedrooms?|rooms?)\b',q,re.I))):
        fields.append('bedrooms')
    if offer.get('suggested_filter_changes') and (
            re.search(r'\b(?:all|those|preferences|criteria|filters)\b',q,re.I)
            or re.search(r'\b(?:area|bathrooms?|parking|kitchen|backyard|garden)\b',q,re.I)):
        fields.append('filters')
    if not fields:return {'action':'none'}
    # The caller uses sales_preferences.apply with the prior offered values and
    # the *new* buyer message as evidence; this function never writes the profile.
    return {'action':'update','unit_id':offer['unit_id'],'fields':fields,
            'offer':offer}


def explicitly_selected_date(text: str, available_dates: list[str]) -> str | None:
    """Resolve a date from dates *already offered*, not from the last slot.

    Handles 'November 24', '24th works' and ISO dates. Never confuse a
    bare clock like '9am' or currency like '$24k' with a day selection.
    """
    from datetime import date
    q=str(text or '').lower()
    if not q:return None
    names=['january','february','march','april','may','june','july',
           'august','september','october','november','december']
    results=[]
    for iso in available_dates:
        try:day=date.fromisoformat(iso)
        except (TypeError,ValueError):continue
        month=names[day.month-1]
        a=month[:3]
        if (iso in q or re.search(r'\b'+month+r'\s+'+str(day.day)+r'(?:st|nd|rd|th)?\b',q) or
            re.search(r'\b'+a+r'\s+'+str(day.day)+r'(?:st|nd|rd|th)?\b',q) or
            re.search(r'\b'+str(day.day)+r'(?:st|nd|rd|th)?\s+'+month+r'\b',q)):
            results.append(iso)
    # 'November 24 and 25' names two distinct days, even when the second
    # omits the repeated month. Neither date is a single committed choice.
    for iso in available_dates:
        try:day=date.fromisoformat(iso)
        except (TypeError,ValueError):continue
        month=names[day.month-1]
        if re.search(r'\b'+month+r'\s+\d{1,2}(?:st|nd|rd|th)?\s*(?:,|and|or)\s*'+
                     str(day.day)+r'(?:st|nd|rd|th)?\b',q):
            results.append(iso)
    if not results and not CLOCK.search(q):
        for iso in available_dates:
            try:day=date.fromisoformat(iso)
            except (TypeError,ValueError):continue
            if re.search(r'(?<![$\w])'+str(day.day)+r'(?:st|nd|rd|th)?\b',q):
                results.append(iso)
    return results[0] if len(set(results))==1 else None
