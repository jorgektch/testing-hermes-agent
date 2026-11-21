"""Pure, dependency-free real estate business functions.

No production customer data is used. Appointments only exist in the caller's
signed, client-held demo session state. Nothing is persisted on Vercel disk.
"""
from __future__ import annotations

import calendar
import copy
import json
import os
import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from datetime import timezone
from . import scheduling

PROJECT_TZ = ZoneInfo('America/Lima')
VISIT_MINUTES = 45
BUFFER_MINUTES = 15
SLOT_MINUTES = VISIT_MINUTES + BUFFER_MINUTES

# The 16 commercial profile keys described by BlackPenguin's current lead dictionary.
BP_COMMERCIAL_FIELDS = frozenset({
    'budget', 'budget_minimum', 'budget_maximum', 'budget_currency',
    'property_interest', 'bedrooms', 'bathrooms', 'location_preference',
    'purchase_timeline', 'financing', 'buyer_type', 'motivation',
    'decision_structure', 'move_in_timing', 'visit_readiness', 'needs',
})

CATALOG_PATH = Path(__file__).resolve().parents[2] / 'shared' / 'catalog.json'
BP_LEAD_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / 'shared' / 'blackpenguin' / 'lead.json'
if not BP_LEAD_TEMPLATE_PATH.is_file():
    BP_LEAD_TEMPLATE_PATH = Path(__file__).resolve().parent / 'lead.json'
# Vercel Services builds backend/ independently. A mirrored catalog is packaged
# under backend/app/ as well; choose it if the monorepo sibling isn't bundled.
if not CATALOG_PATH.is_file():
    CATALOG_PATH = Path(__file__).resolve().parent / 'catalog.json'


def now_local() -> datetime:
    return datetime.now(PROJECT_TZ)


def read_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def read_lead_template() -> dict:
    """Load the source-grounded BlackPenguin draft episode template."""
    return json.loads(BP_LEAD_TEMPLATE_PATH.read_text(encoding='utf-8'))


def blank_state() -> dict:
    bp = read_lead_template()
    lead_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4())
    bp['ids']['lead_id'] = lead_id
    bp['ids']['conversation_id'] = conversation_id
    bp['leads']['id'] = lead_id
    bp['sales_conversations']['id'] = conversation_id
    bp['sales_conversations']['lead_id'] = lead_id
    bp['sales_conversations']['continuity_json']['lead_id'] = lead_id
    bp['sales_dialogue_states']['conversation_id'] = conversation_id
    bp['sales_dialogue_states']['lead_id'] = lead_id
    bp['sales_booking_operations'] = []
    bp['sales_reschedule_operations'] = []
    bp['sales_cancellation_operations'] = []
    bp['sales_conversation_lead_contexts'] = [{
        'conversation_id': conversation_id, 'lead_id': lead_id,
        'is_active': True, 'activated_at': now_local().isoformat(),
    }]
    return {
        'lead': {
            'first_name': None, 'last_name': None, 'email': None, 'phone': None,
            'budget_usd': None, 'purchase_timeframe': None,
            'property_type': None, 'min_bedrooms': None,
            'interest_unit_id': None,
        },
        'stage': 'New Meta lead',
        'shortlisted_unit_ids': [],
        'booked_visits': [],
        'booking_proposal': None,
        'property_interests': [],
        'ui_fact_evidence': {},
        'tool_observations': [],
        'events': [],
        'turn_count': 0,
        'bp_episode': bp,
    }


def record(state: dict, event: str, label: str) -> None:
    state.setdefault('events', []).append({
        'id': str(uuid.uuid4())[:8], 'event': event,
        'label': label, 'timestamp': now_local().isoformat(timespec='seconds'),
    })
    state['events'] = state['events'][-20:]


def begin_turn(state: dict, message: str) -> None:
    """Record the inbound simulated message with the active lead scope."""
    bp = state['bp_episode']
    mid = str(uuid.uuid4())
    state['_active_source_message_id'] = mid
    state['_active_source_message_text'] = message
    bp['sales_messages'].append({
        'id': mid, 'conversation_id': bp['ids']['conversation_id'],
        'role': 'user', 'direction': 'inbound', 'channel': 'simulation',
        'content': message, 'status': 'received',
        'metadata_json': {'lead_id': bp['ids']['lead_id'], 'project_id': bp['ids']['project_id']},
        'created_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
    })
    bp['sales_conversations']['continuity_json']['last_inbound_message_id'] = mid
    bp['sales_conversations']['continuity_json']['waiting_for_lead'] = False
    bp['sales_conversations']['stage'] = 'contacted'
    bp['leads']['funnel_stage'] = 'contacted' if not state['booked_visits'] else 'appointment_set'


def end_turn(state: dict, reply: str) -> None:
    """Record the assistant response and remove transient source text."""
    bp = state['bp_episode']
    bp['sales_messages'].append({
        'id': str(uuid.uuid4()), 'conversation_id': bp['ids']['conversation_id'],
        'role': 'assistant', 'direction': 'outbound', 'channel': 'simulation',
        'content': reply, 'status': 'simulated',
        'metadata_json': {'lead_id': bp['ids']['lead_id'], 'project_id': bp['ids']['project_id']},
        'created_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
    })
    bp['sales_conversations']['continuity_json']['waiting_for_lead'] = True
    state.pop('_active_source_message_id', None)
    state.pop('_active_source_message_text', None)


def _capture_bp_facts(state: dict, changed: dict) -> None:
    """Capture explicitly stated facts with BlackPenguin-style evidence envelopes.

    The UI's nine fields are a convenience projection; the source-grounded
    sales_profile.current/history is the fact ledger, not an eligibility score.
    """
    bp = state['bp_episode']
    record_ = bp['leads']
    sales = record_['lead_profile_data']['sales_profile']
    source_ref={'message_id':state.get('_active_source_message_id')}
    evidence=state.get('_active_source_message_text') or 'Captured through demo tool call (no verbatim message available).'
    fields = {
        'budget_usd': ('budget', lambda v: f'USD {v}'),
        'purchase_timeframe': ('purchase_timeline', str),
        'min_bedrooms': ('bedrooms', int),
        'interest_unit_id': ('property_interest', str),
        **{key: (key, lambda v: v) for key in BP_COMMERCIAL_FIELDS},
    }
    recorded = set()
    for key, val in changed.items():
        if key in ('first_name','last_name'):
            # Persist only if both are explicitly provided; the real Lead table
            # requires full_name, but the draft may still be incomplete.
            l=state['lead']
            if l.get('first_name') and l.get('last_name'):
                record_['full_name']=f"{l['first_name']} {l['last_name']}"
        elif key in ('email','phone'):
            record_[key]=val
        elif key == 'property_type':
            # This demo-only UI preference is not one of the 16 canonical fields.
            record_['lead_profile_data'].setdefault('optional_profile',{})['requested_property_category']=val
        if key not in fields:
            continue
        field, convert=fields[key]
        if field in recorded:
            continue
        recorded.add(field)
        previous=sales['current'].get(field)
        fact={
            'id':str(uuid.uuid4()), 'field':field, 'value':convert(val),
            'status':'declared', 'source':'active_conversation',
            'captured_at':now_local().isoformat(), 'evidence':evidence,
            'source_ref':dict(source_ref),'polarity':'affirmative',
        }
        if field=='buyer_type':
            record_['buyer_type']=str(val)
        if field=='property_interest':
            unit = next((u for u in read_catalog()['properties'] if u['unit_id']==val),None)
            if unit:
                fact['source_ref']['unit_id']=unit['blackpenguin_unit_id']
                fact['source_ref']['property_type_id']=unit['property_type_id']
                bp['ids']['unit_id']=unit['blackpenguin_unit_id']
                bp['ids']['property_type_id']=unit['property_type_id']
                bp['sales_dialogue_states']['state_json']['selected_property_type_id']=unit['property_type_id']
                bp['sales_dialogue_states']['state_json']['selected_unit_id']=unit['blackpenguin_unit_id']
        if previous:
            fact['supersedes']=previous['id']
            sales['history'].append(previous)
            sales['history']=sales['history'][-100:]
        sales['current'][field]=fact
    # Distinguish expressed interest from general discussion or model recommendations.
    if changed.get('interest_unit_id'):
        track_property_interest(state, changed['interest_unit_id'], status='interested')
    # The legacy facts mirror exists in the BP contract for compatibility.
    sales['facts'] = {k:v['value'] for k,v in sales['current'].items() if v['status']=='declared'}
    qualification=record_['lead_profile_data']['active_qualification']
    qualification['missing']=[k for k in ('budget','purchase_timeline') if k not in sales['current']]
    record_['qualification_summary']='; '.join(f'{k}: {v}' for k,v in sales['facts'].items()) or None
    bp['sales_dialogue_states']['revision']+=1
    if not qualification['missing']:
        record_['pipeline_stage']='S02_QUALIFICATION'
        record_['funnel_stage']='qualified'


def parse_target_date(raw: str | None) -> tuple[date | None, str]:
    """Conservatively interpret a lead's intended purchase deadline."""
    if not raw or not str(raw).strip():
        return None, 'Not provided'
    txt = str(raw).strip().lower().replace(',', ' ')
    today = now_local().date()
    if txt in ('asap', 'now', 'immediately', 'immediate'):
        return today, today.isoformat()
    if txt == 'this week':
        target = today + timedelta(days=7)
        return target, target.isoformat()
    if txt in ('next month', 'the next month'):
        serial = today.year * 12 + today.month + 1
        year, month0 = divmod(serial, 12)
        target = date(year, month0 + 1, 1) - timedelta(days=1)
        return target, target.isoformat()
    if txt in ('this year', 'by end of year'):
        return date(today.year, 12, 31), f'{today.year}-12-31'
    if txt in ('next year', 'by next year'):
        return date(today.year + 1, 12, 31), f'{today.year + 1}-12-31'
    found = re.search(r'\b(\d{1,3})\s*(days?|weeks?|months?|years?)\b', txt)
    if found:
        quantity = int(found.group(1))
        unit = found.group(2)
        if quantity > 120:
            return None, 'Please clarify the timeframe'
        if unit.startswith('day'):
            target = today + timedelta(days=quantity)
        elif unit.startswith('week'):
            target = today + timedelta(weeks=quantity)
        else:
            months = quantity * (12 if unit.startswith('year') else 1)
            serial = today.year * 12 + today.month - 1 + months
            year, month0 = divmod(serial, 12)
            target = date(year, month0 + 1, min(today.day, calendar.monthrange(year, month0 + 1)[1]))
        return target, target.isoformat()
    try:
        target = date.fromisoformat(txt)
        return target, target.isoformat()
    except ValueError:
        pass
    if re.fullmatch(r'20\d{2}-\d{2}', txt):
        year, month = map(int, txt.split('-'))
        if 1 <= month <= 12:
            target = date(year, month, calendar.monthrange(year, month)[1])
            return target, target.isoformat()
    quarter = re.fullmatch(r'q([1-4])\s*(20\d{2})', txt)
    if quarter:
        q, year = map(int, quarter.groups())
        month = q * 3
        target = date(year, month, calendar.monthrange(year, month)[1])
        return target, target.isoformat()
    for month, name in enumerate(calendar.month_name):
        if name and re.fullmatch(rf'{name.lower()}\s+20\d{{2}}', txt):
            year = int(txt.split()[-1])
            target = date(year, month, calendar.monthrange(year, month)[1])
            return target, target.isoformat()
    return None, 'Unclear; ask for a month/year or YYYY-MM-DD'


def ready_date(unit: dict) -> date:
    value = unit['available_from_date']
    return now_local().date() if value == 'Immediate' else date.fromisoformat(value)


def project_overview() -> dict:
    return {'ok': True, 'project_info': read_catalog()['project_info']}


def evaluate_properties(state: dict, max_budget_usd: int,
                        target_purchase_timeframe: str | None = None,
                        min_bedrooms: int | None = None,
                        preferred_property_type: str | None = None,
                        required_features: dict | None = None) -> dict:
    try:
        budget = int(max_budget_usd)
        beds = int(min_bedrooms) if min_bedrooms is not None else None
        if budget <= 0 or (beds is not None and (beds < 1 or beds > 20)):
            raise ValueError
    except (TypeError, ValueError):
        return {'error': 'Provide a positive integer budget and valid minimum bedrooms.'}
    allowed_types={'house':'House','apartment':'Apartment','either':None}
    chosen=preferred_property_type or state.get('lead',{}).get('property_type') or 'Either'
    chosen_type=allowed_types.get(str(chosen).strip().lower(),'invalid')
    if chosen_type=='invalid':return {'error':'Preferred property type must be Apartment, House or Either.'}
    filter_features=required_features or {}
    if not isinstance(filter_features,dict):return {'error':'required_features must be a JSON object.'}
    allowed_features={'kitchen_layout','private_backyard','backyard_area_m2',
                      'private_terrace','parking_spaces'}
    if set(filter_features)-allowed_features:
        return {'error':'Unsupported feature filters. Use only confirmed, typed features.'}
    try:
        for key,value in filter_features.items():
            if key in ('parking_spaces','backyard_area_m2'):
                float(value)
            elif key in ('private_backyard','private_terrace') and not isinstance(value,bool):
                raise ValueError('Boolean feature value required')
            elif key=='kitchen_layout' and value not in ('open','closed','semi_open'):
                raise ValueError('Invalid kitchen layout')
    except (ValueError,TypeError):
        return {'error':'Invalid value for typed feature filter.'}
    deadline, interpretation = parse_target_date(target_purchase_timeframe)
    matches, out_budget, other = [], [], []
    for raw_unit in read_catalog()['properties']:
        unit = copy.deepcopy(raw_unit)
        unit['ready_by_purchase_deadline'] = None if deadline is None else ready_date(unit) <= deadline
        if unit['price_usd'] > budget:
            unit['exceeds_budget_by_usd'] = unit['price_usd'] - budget
            out_budget.append(unit)
        elif chosen_type and unit['property_type']!=chosen_type:
            unit['mismatch_reason']='Different property category from the lead preference'
            other.append(unit)
        elif beds and unit['bedrooms'] < beds:
            unit['mismatch_reason'] = 'Fewer bedrooms than requested'
            other.append(unit)
        elif any(
            key not in unit.get('confirmed_features',{}) or
            (float(unit['confirmed_features'][key])!=float(want) if key in ('parking_spaces','backyard_area_m2')
             else unit['confirmed_features'][key]!=want)
            for key,want in filter_features.items()
        ):
            unit['mismatch_reason']='Does not meet all confirmed required features'
            other.append(unit)
        elif deadline is not None and ready_date(unit) > deadline:
            unit['mismatch_reason'] = 'Not ready by the target purchase date'
            other.append(unit)
        else:
            matches.append(unit)
    state['shortlisted_unit_ids'] = [u['unit_id'] for u in matches]
    state['stage'] = 'Properties matched' if matches else 'Exploring alternatives'
    record(state, 'matching', f'{len(matches)} matching properties · ${budget:,} budget')
    return {
        'ok': True, 'matching_properties': matches,
        'out_of_budget_properties': out_budget,
        'other_nonmatching_properties': other,
        'interpreted_purchase_deadline': deadline.isoformat() if deadline else None,
        'timeframe_interpretation': interpretation,
        'note': 'All homes are built residences. Financing details are discussed only at the onsite visit.'
    }


def unit_media(unit_id: str) -> dict:
    unit = next((u for u in read_catalog()['properties']
                 if u['unit_id'].casefold() == str(unit_id).strip().casefold()), None)
    if not unit:
        return {'error': f'Unknown residential unit: {unit_id}'}
    media = unit['media']
    gallery_path = media.get('gallery_url')
    # Never construct URLs from untrusted Host headers. Configure the public
    # domain for custom production domains or Preview environments.
    public_base = os.environ.get('DEMO_PUBLIC_BASE_URL', 'https://testing-hermes-agent-alpha.vercel.app').rstrip('/')
    if not (public_base.startswith('https://') or public_base.startswith('http://localhost:')):
        public_base = 'https://testing-hermes-agent-alpha.vercel.app'
    documents = [d for d in media.get('documents', [])
                 if d.get('scope') == 'unit' and d.get('unit_id') == unit['blackpenguin_unit_id']]
    return {
        'ok': True,
        'unit_id': unit['unit_id'],
        'gallery_url': f'{public_base}{gallery_path}' if gallery_path else None,
        'gallery_available': bool(gallery_path),
        'image_count': len(media.get('gallery_urls', [])),
        'documents': [{'type': d['type'], 'title': d['title'], 'url': f"{public_base}{d['url']}"}
                      for d in documents],
        'unit_pdf_available': bool(documents),
        'note': 'Only approved synthetic unit-specific PDFs are returned. Never substitute a project brochure.'
    }


def date_input(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw == 'today':
        return now_local().date().isoformat()
    if raw == 'tomorrow':
        return (now_local().date() + timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError('Use YYYY-MM-DD, today or tomorrow.') from exc


def slots(state: dict, date_str: str | None = None, *, extra_occupied: dict | None = None) -> dict:
    """Publish three expiring options from a fresh calculation (NOT a hold).

    Offer = client-visible proposal evidence, NOT a meeting. A confirmed
    booking requires another lead message, proposal ID and slot revalidation.
    """
    try:
        day_str=date_input(date_str)
        day=date.fromisoformat(day_str) if day_str else None
    except ValueError as exc:
        return {'error':str(exc)}
    policy=scheduling.policy()
    options=scheduling.pick_diverse_slots(scheduling.available_slots(state,date_filter=day,extra_occupied=extra_occupied),
                                         policy['maximum_offered_options'])
    proposal_id=str(uuid.uuid4())
    expires_at=(datetime.now(timezone.utc)+timedelta(minutes=policy['offer_expiration_minutes'])).isoformat()
    proposal={'version':'hermes-demo/booking-proposal/1.0',
              'proposal_id':proposal_id,'status':'offered','options':options,
              'offer_source_message_id':state.get('_active_source_message_id'),
              'expires_at':expires_at,'timezone':policy['timezone']}
    state['booking_proposal']=proposal
    dialogue=state['bp_episode']['sales_dialogue_states']['state_json']
    dialogue['booking_search']={'requested_date':day_str,'timezone':policy['timezone']}
    dialogue['booking_proposal']=copy.deepcopy(proposal) if options else None
    state['bp_episode']['sales_dialogue_states']['revision']+=1
    record(state,'slot_offer',f'{len(options)} demo visit options · expires in {policy["offer_expiration_minutes"]} minutes')
    return {'ok':True,'available_slots':options,'proposal_id':proposal_id,
            'expires_at':expires_at,'timezone':policy['timezone'],
            'visit_duration_minutes':policy['visit_duration_minutes'],
            'buffer_minutes':policy['buffer_after_minutes'],
            'notice':'Proposed times are NOT held. They expire and must be rechecked. No external calendar is connected.'}


def track_property_interest(state: dict, unit_id: str, status: str = 'discussed') -> dict:
    """Track a named unit with message provenance, never infer buying intent."""
    known = {u['unit_id'] for u in read_catalog()['properties']}
    if unit_id not in known:
        return {'error': 'Unknown or non-public residential unit.'}
    if status not in ('discussed', 'considering', 'interested'):
        return {'error': 'Invalid interest status.'}
    existing = state.setdefault('property_interests', [])
    # A mention cannot downgrade a prior explicit interest.
    rank = {'discussed': 0, 'considering': 1, 'interested': 2}
    previous = next((x for x in existing if x['unit_id'] == unit_id), None)
    if previous is not None:
        if rank[status] >= rank[previous['status']]:
            previous.update(status=status, source_message_id=state.get('_active_source_message_id'))
    else:
        existing.append({'unit_id': unit_id, 'status': status,
                         'source_message_id': state.get('_active_source_message_id')})
    if status == 'interested':
        state['lead']['interest_unit_id'] = unit_id
    return {'ok': True, 'property_interests': existing,
            'active_unit_id': state['lead'].get('interest_unit_id')}


def update_lead(state: dict, **kwargs: Any) -> dict:
    """Update the 9 UI fields and any of BP's 16 declared commercial facts.

    A declared fact is never treated as a verified bank balance, credit result,
    identity or consent. Unknown fields are rejected, not silently discarded.
    """
    lead = state['lead']
    supported = set(lead)
    changed = {}
    bp_changed = {}
    valid_types = {'Apartment', 'House', 'Either'}
    for key, value in kwargs.items():
        if key not in supported and key not in BP_COMMERCIAL_FIELDS:
            return {'error': f'Unknown lead-profile field: {key}'}
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            if len(value) > 200:
                return {'error': f'{key} is too long'}
        if key in ('budget_usd','min_bedrooms','budget_minimum','budget_maximum','bedrooms'):
            try:
                value = int(value)
                max_value = 20 if key in ('min_bedrooms','bedrooms') else 100_000_000
                if not 0 < value < max_value:
                    raise ValueError
            except (TypeError,ValueError):
                return {'error': f'{key} must be a positive integer'}
        if key == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', str(value)):
            return {'error': 'Invalid email format'}
        if key == 'phone' and not re.fullmatch(r'\+?[\d\s()\-]{7,22}', str(value)):
            return {'error': 'Invalid phone format'}
        if key == 'property_type':
            canon = next((x for x in valid_types if x.casefold()==str(value).casefold()),None)
            if canon is None:
                return {'error':'property_type must be Apartment, House or Either'}
            value=canon
        if key == 'interest_unit_id':
            choices={u['unit_id'] for u in read_catalog()['properties']}
            if value not in choices:
                return {'error': f'Unknown available unit: {value}'}
        if key == 'budget_currency':
            value=str(value).upper()
            if not re.fullmatch(r'[A-Z]{3}',value):
                return {'error':'budget_currency must be a 3-letter currency code'}
        if key in supported:
            if lead[key]!=value:
                changed[key]=value
        else:
            previous=state['bp_episode']['leads']['lead_profile_data']['sales_profile']['current'].get(key)
            if not previous or previous['value']!=value:
                bp_changed[key]=value
    # When a canonical field is explicitly collected, update the UI projection.
    aliases={'budget_maximum':'budget_usd','purchase_timeline':'purchase_timeframe',
             'bedrooms':'min_bedrooms'}
    for canonical, ui_field in aliases.items():
        if canonical in bp_changed and ui_field not in changed and lead.get(ui_field)!=bp_changed[canonical]:
            changed[ui_field]=bp_changed[canonical]
    interest=bp_changed.get('property_interest')
    if isinstance(interest,str) and interest in {u['unit_id'] for u in read_catalog()['properties']}:
        if 'interest_unit_id' not in changed and lead.get('interest_unit_id')!=interest:
            changed['interest_unit_id']=interest
    if changed or bp_changed:
        lead.update(changed)
        # UI-only fields (e.g. House vs Apartment) are not part of BP's 16
        # canonical commercial facts. Keep their provenance in the episode too.
        active_message = state.get('_active_source_message_id')
        if active_message:
            for field in changed:
                state.setdefault('ui_fact_evidence', {})[field] = {
                    'source_message_id': active_message,
                    'evidence': state.get('_active_source_message_text') or '',
                    'captured_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                    'source': 'active_conversation',
                }
        # Give explicitly supplied BP-native values priority over legacy alias projections.
        merged=dict(changed)
        canonical_alias={'budget_usd':'budget','purchase_timeframe':'purchase_timeline',
                         'min_bedrooms':'bedrooms','interest_unit_id':'property_interest'}
        for field, value in bp_changed.items():
            for alias, canonical in list(canonical_alias.items()):
                if canonical==field and alias in merged:
                    del merged[alias]
            merged[field]=value
        _capture_bp_facts(state, merged)
        if lead.get('budget_usd') and lead.get('purchase_timeframe'):
            state['stage']='Qualified lead'
        if lead.get('interest_unit_id'):
            state['stage']='Property selected'
        record(state,'lead_update','Updated '+', '.join(k.replace('_',' ') for k in sorted(set(changed)|set(bp_changed))))
    return {'ok':True,'changed_fields':changed,'changed_profile_facts':bp_changed,
            'lead_profile':lead,'stage':state['stage']}


def explicitly_declared_preferences(message: str) -> dict:
    """Conservative deterministic guard for *unambiguous* direct requests.

    Hermes remains responsible for commercial reasoning and calls all regular
    domain tools. This deliberately tiny validator makes a clearly stated
    "2-bedroom house" observable if the model omits update_lead_profile.
    We never infer a budget, a particular unit, financing or buying intent.
    """
    text = str(message or '').strip()
    if not text or '?' in text:
        return {}
    intent = re.search(r"\b(?:i(?:'m| am)\s+looking\s+for|i\s+(?:need|want|prefer)|"
                       r"we(?:'re| are)\s+looking\s+for|i(?:'d| would)\s+like)\b"
                       r"(?P<request>.{1,130})", text, flags=re.I)
    if not intent:
        return {}
    request = intent.group('request').split(';')[0]
    # Uncertainty, negation and alternatives are not safe to assign.
    if re.search(r"\b(?:not|don't|dont|rather than|instead of|either|or|maybe|perhaps|possibly)\b", request, re.I):
        return {}
    kinds = set(x.lower() for x in re.findall(r'\b(?:house|apartment)\b', request, re.I))
    if len(kinds) != 1:
        return {}
    result = {'property_type': 'House' if 'house' in kinds else 'Apartment'}
    beds = re.search(r'\b([1-9]|1[0-9])\s*[- ]?\s*(?:bedroom|bedrooms|bed|beds)\b', request, re.I)
    if not beds:
        beds = re.search(r'\b(?:bedrooms|beds)\s*(?:of|:)?\s*([1-9]|1[0-9])\b', request, re.I)
    if beds:
        result['min_bedrooms'] = int(beds.group(1))
    return result


def verify_explicit_preferences(state: dict, message: str) -> dict:
    """Validate exact declarations through the *existing* domain tool path.

    No second LLM, no speculative profile facts. An audit event distinguishes
    this protective validation from Hermes-originated tool calls.
    """
    values = explicitly_declared_preferences(message)
    missing = {key: val for key, val in values.items() if state['lead'].get(key) != val}
    if not missing:
        return {'ok': True, 'changed_fields': {}}
    response = update_lead(state, **missing)
    if response.get('ok'):
        state.setdefault('tool_observations', []).append({
            'tool': 'explicit_preference_validator', 'status': 'applied',
            'fields': list(missing), 'message_id': state.get('_active_source_message_id'),
            'timestamp': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
        })
        state['tool_observations'] = state['tool_observations'][-50:]
    return response


def _explicit_lead_confirmation(state: dict) -> bool:
    """Demo-only confirmation gate; never rely on LLM-provided consent flags."""
    text=str(state.get('_active_source_message_text') or '').strip().lower()
    if not text or re.search(r'\b(?:not|don.t|never|cancel|no|without)\s+(?:want|confirm|book|schedule|reserve)\b',text):
        return False
    return bool(re.search(r'\b(?:yes|confirm|book|reserve|schedule|go ahead|sounds good|works for me|please do|that works|perfect)\b',text))


def book_visit(state: dict, first_name: str, last_name: str, email: str,
               phone: str, advisor_id: str, date_str: str,
               start_time: str, proposal_id: str, unit_id: str | None = None,
               replaces_meeting_id: str | None = None, extra_occupied: dict | None = None) -> dict:
    """Confirm an offered slot with explicit user consent and immediate recheck.

    This stays an isolated simulation, NOT a multi-user transactional booking.
    """
    policy=scheduling.policy()
    active=[b for b in state['booked_visits'] if b.get('status')=='confirmed']
    if active and (len(active)!=1 or active[0]['meeting_id']!=replaces_meeting_id):
        return {'error':'A confirmed visit already exists. Reschedule or cancel it first.'}
    if replaces_meeting_id and not active:
        return {'error':'No active visit exists to replace.'}
    proposal=state.get('booking_proposal')
    if not proposal or proposal.get('status')!='offered' or proposal.get('proposal_id')!=proposal_id:
        return {'error':'There is no matching active appointment offer. Search slots again.'}
    try:
        expiry=datetime.fromisoformat(proposal['expires_at'].replace('Z','+00:00'))
        if datetime.now(timezone.utc)>=expiry:
            return {'error':'The appointment offer expired. Search current availability again.'}
        day=date_input(date_str)
        start_time=time.fromisoformat(start_time).strftime('%H:%M')
    except (ValueError,TypeError):
        return {'error':'Invalid booking date or time.'}
    if not _explicit_lead_confirmation(state):
        return {'error':'Explicit lead acceptance is required in a new message before a booking can be simulated.'}
    if proposal.get('offer_source_message_id')==state.get('_active_source_message_id'):
        return {'error':'The lead must confirm in a subsequent conversation turn.'}
    option=next((opt for opt in proposal['options'] if opt['advisor_id']==advisor_id
                 and opt['date']==day and opt['start_time']==start_time),None)
    if option is None:
        return {'error':'This option was not offered. Request fresh availability.'}
    available=scheduling.available_slots(state,date_filter=date.fromisoformat(day),extra_occupied=extra_occupied)
    if not any(opt['advisor_id']==advisor_id and opt['date']==day and opt['start_time']==start_time for opt in available):
        return {'error':'This slot is no longer available. Search again.'}
    units={u['unit_id']:u for u in read_catalog()['properties']}
    if unit_id is not None and unit_id not in units:
        return {'error':'Unknown or unavailable property unit. A general project visit may omit unit_id.'}
    if not all(isinstance(x,str) and x.strip() for x in (first_name,last_name,email,phone)):
        return {'error':'First name, last name, email and phone must be provided.'}
    changed=update_lead(state,first_name=first_name,last_name=last_name,email=email,phone=phone,
                        **({'interest_unit_id':unit_id} if unit_id else {}))
    if 'error' in changed:return changed
    selected=units.get(unit_id) if unit_id else None
    start_utc=scheduling.instant(date.fromisoformat(day),start_time)
    visit_end=start_utc+timedelta(minutes=policy['visit_duration_minutes'])
    buffered_end=visit_end+timedelta(minutes=policy['buffer_after_minutes'])
    meeting_id=str(uuid.uuid4());booking_operation_id=str(uuid.uuid4())
    bp=state['bp_episode']; project=read_catalog()['project_info']
    booking={'booking_id':'DEMO-'+meeting_id[:8].upper(), 'meeting_id':meeting_id,
             'proposal_id':proposal_id,'first_name':first_name.strip(), 'last_name':last_name.strip(),
             'email':email.strip(), 'phone':phone.strip(), 'unit_id':unit_id,
             'advisor_id':advisor_id, 'advisor_name':option['advisor_name'],
             'date':day,'start_time':start_time,'end_time':visit_end.astimezone(PROJECT_TZ).strftime('%H:%M'),
             'buffer_until':buffered_end.astimezone(PROJECT_TZ).strftime('%H:%M'),
             'timezone':policy['timezone'],'location':project['location']['onsite_showroom'],
             'visit_duration_minutes':policy['visit_duration_minutes'],'buffer_minutes':policy['buffer_after_minutes'],
             'simulation_only':True,'status':'confirmed'}
    context={'version':'sales-visit-context/1.0','source':'explicit_booking_selection',
             'project_id':bp['ids']['project_id'],'project_name':project['name'],
             'property_type_id':selected['property_type_id'] if selected else None,
             'property_type_name':selected['commercial_name'] if selected else None,
             'unit_id':selected['blackpenguin_unit_id'] if selected else None,
             'unit_code':selected['unit_id'] if selected else None,
             'timezone':policy['timezone'],
             'property_association':'explicit' if selected else 'unknown'}
    meeting={'id':meeting_id,'project_id':bp['ids']['project_id'],'lead_id':bp['ids']['lead_id'],
             'broker_id':None,'assigned_sales_user_id':option['sales_user_id'],
             'meeting_time':start_utc.isoformat().replace('+00:00','Z'),
             'meeting_timezone':policy['timezone'],'duration_minutes':policy['visit_duration_minutes'],
             'buffer_after_minutes':policy['buffer_after_minutes'],
             'modality':'showroom','confirmation_status':'confirmed','calendar_sync_status':'simulation_ready',
             'visit_location_label':'Synthetic project showroom','visit_address':project['location']['street_address'],
             'visit_property_type_id':context['property_type_id'],'visit_unit_id':context['unit_id'],
             'visit_context':context,'location_confirmation_status':'confirmed',
             'location_confirmed_by':'synthetic_demo_fixture',
             'status':'confirmed','source':'agent_simulation','is_demo':True,
             'gcal_event_id':None,'created_at':datetime.now(timezone.utc).isoformat()}
    operation={'id':booking_operation_id,'company_id':bp['ids']['company_id'],
               'project_id':bp['ids']['project_id'],'conversation_id':bp['ids']['conversation_id'],
               'lead_id':bp['ids']['lead_id'],'proposal_id':proposal_id,'meeting_id':meeting_id,
               'snapshot_json':{'version':'hermes-demo/booking-receipt/1.0','context':context,
                                'option':{'starts_at':meeting['meeting_time'],'sales_user_id':option['sales_user_id']},
                                'preferences':{'duration_minutes':policy['visit_duration_minutes'],'buffer_after_minutes':policy['buffer_after_minutes']}},
               'calendar_status':'simulation','calendar_attempts':0,'next_attempt_at':None,
               'created_at':datetime.now(timezone.utc).isoformat()}
    bp['meetings'].append(meeting);bp['sales_booking_operations'].append(operation)
    state['booked_visits'].append(booking)
    proposal['status']='confirmed';proposal['meeting_id']=meeting_id
    bp['sales_dialogue_states']['state_json']['booking_proposal']=None
    bp['leads']['funnel_stage']='appointment_set'
    bp['leads']['pipeline_stage']='S04_APPOINTMENT'
    state['stage']='Visit booked · simulated'
    record(state,'booking',f'Confirmed demo visit {day} at {start_time} · {option["advisor_name"]}')
    return {'ok':True,'booking':booking,'meeting':{'id':meeting_id,'status':'confirmed','calendar_sync_status':'simulation_ready'},
            'notice':'Simulation only. No external calendar synced and no real advisor notified.'}


def cancel_visit(state: dict, meeting_id: str, reason: str | None = None) -> dict:
    """Record one simulated cancellation with a linked operation receipt."""
    text=str(state.get('_active_source_message_text') or '').strip().lower()
    if not re.search(r'\b(?:cancel|please cancel|call off)\b',text) or re.search(r"\b(?:don.t|do not|not)\s+cancel\b",text):
        return {'error':'A direct cancellation request from the lead is required.'}
    bp=state['bp_episode'];meeting=next((m for m in bp['meetings'] if m['id']==meeting_id and m['status']=='confirmed' and m['lead_id']==bp['ids']['lead_id']),None)
    if not meeting:return {'error':'No active confirmed visit for this meeting_id.'}
    meeting['status']='cancelled';meeting['confirmation_status']='cancelled_by_lead'
    booking=next((b for b in state['booked_visits'] if b['meeting_id']==meeting_id),None)
    if booking:booking['status']='cancelled'
    operation={'id':str(uuid.uuid4()),'company_id':bp['ids']['company_id'],
               'project_id':bp['ids']['project_id'],'conversation_id':bp['ids']['conversation_id'],
               'lead_id':bp['ids']['lead_id'],'proposal_id':str(uuid.uuid4()),
               'meeting_id':meeting_id,'reason':reason,
               'reason_message_id':state.get('_active_source_message_id'),
               'calendar_status':'simulation','notification_status':'simulation',
               'snapshot_json':{'version':'hermes-demo/cancellation/1.0','meeting_id':meeting_id,
                                'original_start_utc':meeting['meeting_time']},
               'created_at':datetime.now(timezone.utc).isoformat()}
    bp['sales_cancellation_operations'].append(operation)
    state['stage']='Visit cancelled · simulated'
    bp['leads']['funnel_stage']='qualified'
    record(state,'cancellation',f'Cancelled simulated visit {meeting_id[:8]}')
    return {'ok':True,'meeting_id':meeting_id,'status':'cancelled',
            'notice':'Only this browser simulation was changed; no real calendar or notification exists.'}


def reschedule_visit(state: dict, original_meeting_id: str, first_name: str, last_name: str,
                     email: str, phone: str, advisor_id: str, date_str: str, start_time: str,
                     proposal_id: str, unit_id: str | None = None, *, extra_occupied: dict | None = None) -> dict:
    """New meeting replaces previous: old Meeting is cancelled, history preserved."""
    bp=state['bp_episode']
    old=next((m for m in bp['meetings'] if m['id']==original_meeting_id and m['status']=='confirmed'
              and m['lead_id']==bp['ids']['lead_id']),None)
    if old is None:return {'error':'No matching active visit to reschedule.'}
    if unit_id is None:
        prior=next((b for b in state['booked_visits'] if b['meeting_id']==original_meeting_id),None)
        unit_id=prior['unit_id'] if prior else None
    new=book_visit(state,first_name,last_name,email,phone,advisor_id,date_str,start_time,
                   proposal_id,unit_id=unit_id,replaces_meeting_id=original_meeting_id,extra_occupied=extra_occupied)
    if 'error' in new:return new
    old['status']='cancelled';old['confirmation_status']='rescheduled'
    prior=next((b for b in state['booked_visits'] if b['meeting_id']==original_meeting_id),None)
    if prior:prior['status']='rescheduled'
    latest=bp['sales_booking_operations'][-1]
    bp['sales_reschedule_operations'].append({
        'id':str(uuid.uuid4()), 'company_id':bp['ids']['company_id'],
        'project_id':bp['ids']['project_id'],'conversation_id':bp['ids']['conversation_id'],
        'lead_id':bp['ids']['lead_id'],'proposal_id':proposal_id,
        'original_meeting_id':original_meeting_id,
        'replacement_meeting_id':new['meeting']['id'],
        'replacement_booking_operation_id':latest['id'],
        'snapshot_json':{'version':'hermes-demo/reschedule/1.0',
                         'original':{'meeting_id':original_meeting_id,'starts_at':old['meeting_time']},
                         'replacement':{'meeting_id':new['meeting']['id'],
                                        'starts_at':bp['meetings'][-1]['meeting_time']}},
        'calendar_status':'simulation','created_at':datetime.now(timezone.utc).isoformat()})
    state['stage']='Visit rescheduled · simulated'
    record(state,'reschedule',f'Rescheduled simulated visit to {date_str} {start_time}')
    return {'ok':True,'original_meeting_id':original_meeting_id,
            'replacement_meeting_id':new['meeting']['id'], 'booking':new['booking'],
            'notice':'Simulation only; original appointment was cancelled in this session and a replacement was created.'}


def profile_snapshot(state: dict) -> dict:
    """Small, read-only UI projection for in-turn Hermes tool events.

    The complete state and Hermes history never enter live event payloads.
    The server only commits the episode atomically after the agent completes.
    """
    bp = state.get('bp_episode') or {}
    facts = (bp.get('leads') or {}).get('lead_profile_data', {}).get('sales_profile', {}).get('current', {})
    return {
        'lead': dict(state.get('lead') or {}),
        'stage': state.get('stage'),
        'property_interests': list(state.get('property_interests') or []),
        'ui_fact_evidence': dict(state.get('ui_fact_evidence') or {}),
        'facts': {k: dict(v) for k,v in facts.items()},
        'tool_observations': list(state.get('tool_observations') or [])[-8:],
    }
