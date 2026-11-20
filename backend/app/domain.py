"""Pure, dependency-free real estate business functions.

No production customer data is used. Appointments only exist in the caller's
signed, client-held demo session state. Nothing is persisted on Vercel disk.
"""
from __future__ import annotations

import calendar
import copy
import json
import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_TZ = ZoneInfo('America/Lima')
VISIT_MINUTES = 45
BUFFER_MINUTES = 15
SLOT_MINUTES = VISIT_MINUTES + BUFFER_MINUTES
CATALOG_PATH = Path(__file__).resolve().parents[2] / 'shared' / 'catalog.json'
# Vercel Services builds backend/ independently. A mirrored catalog is packaged
# under backend/app/ as well; choose it if the monorepo sibling isn't bundled.
if not CATALOG_PATH.is_file():
    CATALOG_PATH = Path(__file__).resolve().parent / 'catalog.json'


def now_local() -> datetime:
    return datetime.now(PROJECT_TZ)


def read_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def blank_state() -> dict:
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
        'events': [],
        'turn_count': 0,
    }


def record(state: dict, event: str, label: str) -> None:
    state.setdefault('events', []).append({
        'id': str(uuid.uuid4())[:8], 'event': event,
        'label': label, 'timestamp': now_local().isoformat(timespec='seconds'),
    })
    state['events'] = state['events'][-20:]


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
                        min_bedrooms: int | None = None) -> dict:
    try:
        budget = int(max_budget_usd)
        beds = int(min_bedrooms) if min_bedrooms is not None else None
        if budget <= 0 or (beds is not None and (beds < 1 or beds > 20)):
            raise ValueError
    except (TypeError, ValueError):
        return {'error': 'Provide a positive integer budget and valid minimum bedrooms.'}
    deadline, interpretation = parse_target_date(target_purchase_timeframe)
    matches, out_budget, other = [], [], []
    for raw_unit in read_catalog()['properties']:
        unit = copy.deepcopy(raw_unit)
        unit['ready_by_purchase_deadline'] = None if deadline is None else ready_date(unit) <= deadline
        if unit['price_usd'] > budget:
            unit['exceeds_budget_by_usd'] = unit['price_usd'] - budget
            out_budget.append(unit)
        elif beds and unit['bedrooms'] < beds:
            unit['mismatch_reason'] = 'Fewer bedrooms than requested'
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
    return {
        'ok': True, 'unit_id': unit['unit_id'],
        'gallery_urls': media['gallery_urls'],
        'floorplan_brochure_pdf': media['floorplan_brochure_pdf'],
        'gallery_available': bool(media['gallery_urls']),
        'floorplan_pdf_available': bool(media['floorplan_brochure_pdf']),
        'note': 'Images and PDF brochures are synthetic demo documents.'
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


def slots(state: dict, date_str: str | None = None) -> dict:
    """Return 45-minute appointments spaced one hour apart (15-minute buffer)."""
    try:
        day_filter = date_input(date_str)
    except ValueError as exc:
        return {'error': str(exc)}
    booked = {(b['advisor_id'], b['date'], b['start_time']) for b in state['booked_visits']}
    now = now_local()
    dates = [(now.date() + timedelta(days=days)).isoformat() for days in range(1, 15)]
    result = []
    for advisor in read_catalog()['sales_advisors']:
        for offset_text, ranges in advisor['schedule_template'].items():
            day = dates[int(offset_text) - 1]
            if day_filter and day != day_filter:
                continue
            for slot_range in ranges:
                begin, finish = slot_range.split('-', 1)
                cursor = datetime.combine(date.fromisoformat(day), time.fromisoformat(begin), tzinfo=PROJECT_TZ)
                ending = datetime.combine(date.fromisoformat(day), time.fromisoformat(finish), tzinfo=PROJECT_TZ)
                while cursor + timedelta(minutes=SLOT_MINUTES) <= ending:
                    start_hhmm = cursor.strftime('%H:%M')
                    if cursor > now and (advisor['advisor_id'], day, start_hhmm) not in booked:
                        result.append({
                            'advisor_id': advisor['advisor_id'], 'advisor_name': advisor['name'],
                            'specialty': advisor['specialty'], 'date': day,
                            'start_time': start_hhmm,
                            'visit_end_time': (cursor + timedelta(minutes=VISIT_MINUTES)).strftime('%H:%M'),
                            'buffer_end_time': (cursor + timedelta(minutes=SLOT_MINUTES)).strftime('%H:%M'),
                        })
                    cursor += timedelta(minutes=SLOT_MINUTES)
    result.sort(key=lambda x: (x['date'], x['start_time'], x['advisor_id']))
    return {'ok': True, 'timezone': 'America/Lima', 'visit_duration_minutes': VISIT_MINUTES,
            'buffer_minutes': BUFFER_MINUTES, 'available_slots': result}


def update_lead(state: dict, **kwargs: Any) -> dict:
    """Validate and record the facts explicitly provided by the lead."""
    lead = state['lead']
    supported = set(lead)
    changed = {}
    valid_types = {'Apartment', 'House', 'Either'}
    for key, value in kwargs.items():
        if key not in supported or value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            if len(value) > 200:
                return {'error': f'{key} is too long'}
        if key in ('budget_usd', 'min_bedrooms'):
            try:
                value = int(value)
                if not 0 < value < (100_000_000 if key == 'budget_usd' else 21):
                    raise ValueError
            except (ValueError, TypeError):
                return {'error': f'{key} must be a positive integer'}
        if key == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            return {'error': 'Invalid email format'}
        if key == 'phone' and not re.fullmatch(r'\+?[\d\s()\-]{7,22}', value):
            return {'error': 'Invalid phone format'}
        if key == 'property_type':
            canon = next((x for x in valid_types if x.casefold() == str(value).casefold()), None)
            if canon is None:
                return {'error': 'property_type must be Apartment, House or Either'}
            value = canon
        if key == 'interest_unit_id':
            choices = {u['unit_id'] for u in read_catalog()['properties']}
            if value not in choices:
                return {'error': f'Unknown unit: {value}'}
        if lead[key] != value:
            changed[key] = value
    if changed:
        lead.update(changed)
        if lead.get('budget_usd') and lead.get('purchase_timeframe'):
            state['stage'] = 'Qualified lead'
        if lead.get('interest_unit_id'):
            state['stage'] = 'Property selected'
        record(state, 'lead_update', 'Updated ' + ', '.join(k.replace('_',' ') for k in changed))
    return {'ok': True, 'changed_fields': changed, 'lead_profile': lead, 'stage': state['stage']}


def book_visit(state: dict, first_name: str, last_name: str, email: str,
               phone: str, unit_id: str, advisor_id: str, date_str: str,
               start_time: str) -> dict:
    """Book one simulated visit per session; recheck the hour slot first."""
    if state['booked_visits']:
        return {'error': 'This demo session already has a confirmed visit.'}
    units = {u['unit_id'] for u in read_catalog()['properties']}
    advisors = {a['advisor_id'] for a in read_catalog()['sales_advisors']}
    if unit_id not in units or advisor_id not in advisors:
        return {'error': 'Unknown property or advisor.'}
    try:
        day = date_input(date_str)
        start_time = time.fromisoformat(start_time).strftime('%H:%M')
    except ValueError:
        return {'error': 'Invalid booking date or start time.'}
    available = slots(state, day)
    selected = next((s for s in available.get('available_slots', [])
                     if s['advisor_id'] == advisor_id and s['start_time'] == start_time), None)
    if not selected:
        return {'error': 'That advisor timeslot is not available. Refresh available slots.'}
    updated = update_lead(state, first_name=first_name, last_name=last_name,
                          email=email, phone=phone, interest_unit_id=unit_id)
    if 'error' in updated:
        return updated
    booked = {
        'booking_id': 'DEMO-' + uuid.uuid4().hex[:8].upper(),
        'first_name': first_name.strip(), 'last_name': last_name.strip(),
        'email': email.strip(), 'phone': phone.strip(),
        'unit_id': unit_id, 'advisor_id': advisor_id,
        'advisor_name': selected['advisor_name'], 'date': day,
        'start_time': start_time, 'end_time': selected['visit_end_time'],
        'buffer_until': selected['buffer_end_time'], 'timezone': 'America/Lima',
        'location': 'Sales showroom at project entrance · Santiago de Surco, Lima, Peru',
        'visit_duration_minutes': VISIT_MINUTES,
        'simulation_only': True,
    }
    state['booked_visits'].append(booked)
    state['stage'] = 'Visit booked · simulated'
    record(state, 'booking', f"Visit simulated · {day} at {start_time}")
    return {'ok': True, 'booking': booked,
            'notice': 'Simulation only. No human advisor or external calendar was notified.'}
