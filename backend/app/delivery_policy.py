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



def _no_matches_for_state(state: dict) -> str:
    """Catalog-grounded commercial reply; hard buyer limits stay unchanged."""
    prefs=(state.get('active_search') or {}).get('effective_preferences') or state.get('search_preferences') or {}
    budget=prefs.get('budget') or {}
    bedrooms=prefs.get('bedrooms') or {}
    cap=budget.get('maximum_usd')
    beds=bedrooms.get('preferred') or bedrooms.get('minimum')
    requested=(f'{beds}-bedroom house' if beds else 'house')
    scope=(f' within your ${int(cap):,} budget' if cap is not None else
           ' matching those preferences')
    intro=f"We don't currently have a {requested}{scope}."
    ranked=state.get('_search_out_of_budget_this_turn') or []
    ranked=sorted(ranked,key=lambda u:(
        int(beds is not None and u['bedrooms']!=beds),
        int(u.get('exceeds_budget_by_usd') or 0),
        int(u['price_usd']),u['unit_id']))
    if cap is not None and ranked:
        alternative=ranked[0]
        delta=int(alternative['price_usd'])-int(cap)
        if delta>0:
            other_beds=(f'{alternative["bedrooms"]}-bedroom ' if
                        beds and alternative['bedrooms']!=beds else '')
            return (f'{intro} The lowest-priced {other_beds}alternative is '
                    f'{alternative["unit_id"]} at ${int(alternative["price_usd"]):,}, '
                    f'${delta:,} above your limit. '
                    'Would you like to see it as an alternative, or keep your budget unchanged?')
    return intro+' Would you like to adjust a criterion?'


_TECHNICAL_REPLY=re.compile(
    r'\b(?:system (?:requires|issue|error)|technical (?:issue|difficulty|error)|'
    r'(?:difficulty|trouble|problem) (?:retrieving|accessing|fetching)|'
    r'unable to (?:retrieve|access|fetch) (?:properties|inventory)|'
    r'constraints for filtering|try(?:ing)? (?:the )?search again|'
    r'confirm that you want to keep the preference|'
    r'(?:internal|validation|tool|backend) error)\b',re.I)


def _search_answer_needs_repair(plain: str, state: dict, matches: list[dict]) -> bool:
    if _TECHNICAL_REPLY.search(plain):
        return True
    codes={c.upper() for c in re.findall(r'\bH-\d+\b',plain,re.I)}
    valid={u['unit_id'] for u in matches}
    extras=set(state.get('_allowed_alternative_units_this_turn') or [])
    closest=state.get('_search_nearest_out_of_budget_this_turn') or {}
    if not matches and closest.get('unit_id'):
        extras.add(closest['unit_id'])
    if codes-valid-extras:
        return True
    if not matches:
        if not re.search(
            r'\b(?:no (?:houses?|homes?|properties|matches|listings|options)|'
            r"don.t (?:currently )?have|do not (?:currently )?have|"
            r"couldn.t find|cannot find|can't find|none (?:meet|match)|"
            r'not (?:currently )?available)\b',plain,re.I):
            return True
        if codes.intersection(extras) and not re.search(
            r'\b(?:above|over|exceeds|outside|alternative|more than|higher)\b',
            plain,re.I):
            return True
    return False



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


def _verified_fact_reply(state: dict, plain: str) -> str | None:
    """Repair an unanswered, single-property public fact without extra consent."""
    from . import conversation_context
    question=str(state.get('_active_source_message_text') or '')
    # A price mention inside an alternative search is NOT a question about
    # the previously discussed property's price.
    if state.get('_turn_intent') in ('property_search','property_comparison','preference_update'):
        return None
    topic=conversation_context.topic(question) or state.get('_pending_factual_topic_override')
    original_question=state.get('_pending_factual_request_text') or question
    if topic not in ('area','price','bedrooms','bathrooms','kitchen','backyard',
                     'parking','location','availability'):
        return None
    resolution=state.get('_turn_property_resolution') or {}
    facts=state.get('_details_for_current_turn') or {}
    code=resolution.get('unit_id')
    if (not code or resolution.get('ambiguous') or facts.get('unit_id')!=code):
        return None
    features=facts.get('confirmed_features') or {}
    plausible={
        'area':bool(re.search(r'\b(?:area|size|sq\.?\s*m|m2|sq\.?\s*ft|square feet)\b',plain,re.I)
                    and any(str(v) in plain for v in
                            (facts.get('area_sqm'),facts.get('lot_area_m2')))),
        'price':str(facts['price_usd']) in plain.replace(',','') or
                f'{int(facts["price_usd"])//1000}k' in plain.lower(),
        'bedrooms':bool(re.search(r'\b(?:bedrooms?|beds?)\b',plain,re.I)
                        and re.search(r'(?<![\w-])'+str(facts['bedrooms'])+r'(?!\d)',plain)),
        'bathrooms':bool(re.search(r'\b(?:bathrooms?|baths?)\b',plain,re.I)
                         and str(facts['bathrooms']) in plain),
        'kitchen':bool(re.search(r'\bkitchen\b',plain,re.I)
                        and str(features.get('kitchen_layout','')).lower() in plain.lower()),
        'backyard':bool(re.search(r'\b(?:backyard|garden)\b',plain,re.I)),
        'parking':bool(re.search(r'\b(?:parking|car space)\b',plain,re.I)
                        and str(features.get('parking_spaces','')) in plain),
        'location':bool(re.search(r'\blima\b',plain,re.I)
                         and re.search(r'\b(?:city|reference|exact|fictional)\b',plain,re.I)),
        'availability':bool(str(facts['available_from_date']).lower() in plain.lower() or
                            (facts['available_from_date']=='Immediate' and
                             'ready now' in plain.lower())),
    }
    stalled=bool(re.search(
        r'\b(?:please confirm|confirm again|could you confirm|'
        r'are you interested|system issue|unable to (?:access|retrieve|confirm)|'
        r'would you like to proceed|still need to confirm)\b',plain,re.I))
    needs_feet=(topic=='area' and bool(re.search(
        r'\b(?:square footage|square feet|sq\.?\s*ft)\b',original_question,re.I)) and
        not bool(re.search(r'\b(?:square feet|sq\.?\s*ft)\b',plain,re.I)))
    if plausible.get(topic) and not stalled and not needs_feet:
        return None
    name=f'{facts["commercial_name"]} ({code})'
    if topic=='area':
        built=float(facts['area_sqm']); lot=float(facts['lot_area_m2'])
        def unit(n):
            return int(n) if n.is_integer() else n
        return (f'{name}: built area {unit(built)} sq m '
                f'({round(built*10.763910417):,} sq ft); lot area '
                f'{unit(lot)} sq m ({round(lot*10.763910417):,} sq ft).')
    if topic=='price':
        return f'{name} is listed for USD {int(facts["price_usd"]):,} (synthetic).'
    if topic=='bedrooms':
        return f'{name} has {facts["bedrooms"]} bedrooms.'
    if topic=='bathrooms':
        return f'{name} has {facts["bathrooms"]} bathrooms.'
    if topic=='kitchen':
        return f'{name} has an {features.get("kitchen_layout","unspecified")} kitchen.'
    if topic=='backyard':
        return (f'{name} has a private backyard of {features.get("backyard_area_m2")} sq m.'
                if features.get('private_backyard') else
                f'{name} has no verified private backyard.')
    if topic=='parking':
        return f'{name} has {features.get("parking_spaces","unspecified")} parking space(s).'
    if topic=='location':
        city=(facts.get('location') or {}).get('city') or 'Lima'
        return (f'{name} is listed in {city}, Peru (synthetic). '
                'Its exact street address is not published.')
    if topic=='availability':
        when=facts['available_from_date']
        return f'{name} is {"ready now" if when=="Immediate" else "available from "+when}.'
    return None



def _suggest_available_assets(state: dict, message: str) -> str:
    """Offer approved *resource names* once, never unsolicited links."""
    from . import domain
    question=str(state.get('_active_source_message_text') or '')
    if state.get('_turn_intent') in (
            'property_search','property_comparison','booking_action','visit_planning'):
        return message
    resolved=state.get('_turn_property_resolution') or {}
    code=resolved.get('unit_id')
    if resolved.get('ambiguous') or code not in domain._units_by_code():
        return message
    media=domain.unit_media(code)
    if media.get('error'):
        return message
    sent=(state.get('shared_media') or {}).get(code) or {}
    offered=state.setdefault('offered_assets',{}).setdefault(code,[])
    photo_requested=bool(re.search(r'\b(?:photos?|gallery|pictures?|images?)\b',question,re.I))
    media_requested=photo_requested or bool(re.search(
        r'\b(?:pdfs?|brochures?|plans?|documents?|fact.?sheets?)\b',question,re.I))
    broad_detail=bool(re.search(
        r'\b(?:tell me more|details|about (?:it|this|the)|features|characteristics)\b',question,re.I))
    if not (broad_detail or (media_requested and _urls(message))):
        return message
    suggestions=[]
    sent_docs=set(sent.get('document_urls_sent') or [])
    if media.get('documents') and 'documents' not in offered:
        remaining=[d for d in media['documents'] if d['url'] not in sent_docs]
        if remaining:
            label=('PDF fact sheet' if any(
                (d.get('type')=='fact_sheet' or
                'fact sheet' in str(d.get('title','')).lower()) for d in remaining)
                else 'property PDF')
            if label.lower() not in message.lower():
                suggestions.append(label)
            offered.append('documents')
    if media.get('gallery_available') and 'gallery' not in offered:
        if not sent.get('gallery_sent') and not photo_requested and 'gallery' not in message.lower():
            suggestions.append('photos')
        offered.append('gallery')
    if not suggestions:
        return message
    return message.rstrip()+' I can also share '+(' and '.join(suggestions))+f' for {code} if you would like.'



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
    commercial_search=state.get('_turn_intent') in ('property_search','preference_update')
    if unauthorised:
        if search_results is not None:
            return (_no_matches_for_state(state) if not search_results
                    else _matching_reply(search_results))
        # Do not turn a bad link into a plausible but unverifiable address.
        raise ValueError('Hermes returned a URL not authorized by the domain media tool.')
    # The language model cannot promote a selected hour to a confirmed visit.
    draft=state.get('visit_draft') or {}
    active_user_text=str(state.get('_active_source_message_text') or '')
    if state.get('_booking_completion_error'):
        return ('Your requested visit could not be confirmed: ' +
                state['_booking_completion_error'] +
                ' Your existing appointment, if any, is unchanged. Can I check another time?')
    if (draft.get('status')=='awaiting_confirmation' and
            state.get('_turn_intent') not in ('property_search','property_comparison','preference_update')):
        false_claim=bool(re.search(
            r"\b(?:is confirmed|has been confirmed|i(?:'ve| have) (?:booked|scheduled)|"
            r"your visit is booked|your booking is confirmed)\b",plain,re.I))
        dates=re.findall(r'\b20\d{2}-\d{2}-\d{2}\b',plain)
        wrong_day=bool(dates and any(d!=draft.get('date') for d in dates))
        just_selected=bool(re.search(r'\b\d{1,2}(?::00)?\s*(?:am|pm)\b',active_user_text,re.I))
        if false_claim or wrong_day or just_selected:
            return _pending_visit_recap(state)
    # A missing/incorrect Hermes search-tool call must not return the price
    # of the *previously* discussed home. Recover from server-verified catalog
    # only when the buyer clearly requested a new search.
    if commercial_search and search_results is None:
        from . import domain
        outcome=domain.search_properties(state)
        if outcome.get('ok'):
            search_results=outcome['matching_properties']
            state['_automatic_search_recovery']=True
            state.setdefault('tool_observations',[]).append({
                'tool':'search_response_recovery','status':'applied',
                'fields':['property_search'],
                'message_id':state.get('_active_source_message_id'),
                'timestamp':domain.now_local().isoformat()})
    if commercial_search and search_results is not None:
        if state.get('_automatic_search_recovery') or _search_answer_needs_repair(
                plain,state,search_results):
            return (_no_matches_for_state(state) if not search_results
                    else _matching_reply(search_results))
    repaired=_verified_fact_reply(state,plain)
    if repaired is not None:
        state['_fact_answer_repaired_this_turn']=True
        return repaired
    repeat_urls=set(state.get('_media_resend_urls_this_turn') or [])
    if repeat_urls.intersection(_urls(plain)) and not re.search(
            r'\b(?:resend|resending|again|previously shared|reenv[ií]o)\b',plain,re.I):
        unit=(state.get('_turn_property_resolution') or {}).get('unit_id')
        plain=f"I'm resending the requested material for {unit or 'this house'}: "+plain
    if search_results is not None:
        names={x.upper() for x in re.findall(r'\bH-\d+\b',plain,re.I)}
        matches={u['unit_id'] for u in search_results}
        alternatives=set(state.get('_allowed_alternative_units_this_turn') or [])
        if commercial_search and not search_results:
            closest=state.get('_search_nearest_out_of_budget_this_turn') or {}
            if closest.get('unit_id'):
                alternatives.add(closest['unit_id'])
        if (not names.issubset(matches|alternatives) or
                sms.details(plain)['segments']>2 or
                (bool(names.intersection(alternatives)) and not re.search(
                    r'\b(?:optional|alternative|different|extra)\b',plain,re.I))):
            return (_no_matches_for_state(state) if not search_results
                    else _matching_reply(search_results))
    _record_sent_media(state,plain)
    return _suggest_available_assets(state,plain)
