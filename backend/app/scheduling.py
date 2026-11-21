"""Deterministic, read-only slot projection from BlackPenguin-inspired fixtures.

This is a *simulation*, not a concurrency-safe or shared-calendar booking service.
A slot is never stored as a durable row. One advisor's occupancy applies across
all projects, and the calculated occupied interval includes the post-visit buffer.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo('America/Lima')
UTC = timezone.utc
ACTIVE_STATUSES = {'scheduled','confirmed','in_progress'}


def _read(name: str) -> dict:
    return json.loads((HERE/name).read_text(encoding='utf-8'))


def policy() -> dict:
    return _read('scheduling_policy.json')


def team() -> dict:
    return _read('team.json')


def availability() -> dict:
    return _read('availability.json')


def local_now() -> datetime:
    return datetime.now(TZ)


def offset_day(offset: int, today: date | None = None) -> date:
    return (today or local_now().date()) + timedelta(days=offset)


def instant(day: date, clock: str, tz: ZoneInfo = TZ) -> datetime:
    """Convert demo local wall-time to a timezone-aware UTC instant."""
    return datetime.combine(day, time.fromisoformat(clock), tzinfo=tz).astimezone(UTC)


def overlaps(start: datetime, end: datetime, occupied_start: datetime, occupied_end: datetime) -> bool:
    return start < occupied_end and end > occupied_start


def _week_intervals(user_id: str, day: date, data: dict) -> list[tuple[datetime,datetime]]:
    result=[]
    for window in data['sales_availability_windows']:
        if (window['user_id']==user_id and window['is_active'] and window['weekday']==day.weekday()):
            zone=ZoneInfo(window['timezone'])
            start,end=instant(day,window['start_time'],zone),instant(day,window['end_time'],zone)
            if end>start:result.append((start,end))
    return result


def _exception_intervals(user_id: str, day: date, data: dict, today: date) -> tuple[list, list]:
    extra=[]; blocked=[]
    for exc in data['sales_availability_exceptions']:
        if exc['user_id']!=user_id or offset_day(exc['day_offset'],today)!=day:
            continue
        zone=ZoneInfo(exc['timezone'])
        interval=(instant(day,exc['start_time'],zone), instant(day,exc['end_time'],zone))
        (extra if exc['kind']=='extra_availability' else blocked).append(interval)
    return extra,blocked


def _occupied_intervals(user_id: str, state: dict, data: dict, today: date, buffer: int, extra_occupied: dict | None = None) -> list[tuple[datetime,datetime]]:
    spans=[]
    for meeting in ([] if extra_occupied is not None else _read('meetings.json')['demo_existing_meetings']):
        if meeting['assigned_sales_user_id'] != user_id or meeting['status'] not in ACTIVE_STATUSES:
            continue
        start=instant(offset_day(meeting['day_offset'],today),meeting['local_start_time'])
        spans.append((start,start+timedelta(minutes=meeting['duration_minutes']+meeting.get('buffer_after_minutes',buffer))))
    for meeting in state.get('bp_episode',{}).get('meetings',[]):
        if meeting.get('assigned_sales_user_id')!=user_id or meeting.get('status') not in ACTIVE_STATUSES:
            continue
        start=datetime.fromisoformat(meeting['meeting_time'].replace('Z','+00:00'))
        if start.tzinfo is None:start=start.replace(tzinfo=UTC)
        start=start.astimezone(UTC)
        spans.append((start,start+timedelta(minutes=meeting['duration_minutes']+meeting.get('buffer_after_minutes',buffer))))
    if extra_occupied is not None:
        spans.extend(extra_occupied.get(user_id, []))
    return spans


def eligible_users(project_id: str) -> list[dict]:
    data=team()
    active={a['id']:a for a in data['users'] if a['company_id']==data['company_id'] and a['is_active'] and a['role'] in ('admin','sales')}
    return [active[asgmt['user_id']] for asgmt in data['project_user_assignments']
            if asgmt['project_id']==project_id and asgmt['is_active'] and asgmt['sales_enabled']
            and asgmt['accepts_appointments'] and asgmt['user_id'] in active]


def available_slots(state: dict, *, date_filter: date | None = None, now: datetime | None = None,
                    limit: int | None = None, extra_occupied: dict | None = None,
                    include_today: bool = False) -> list[dict]:
    """Only project-eligible advisors, global occupancy, and explicit blocked exceptions.

    A search window can be every 30m even though each accepted meeting blocks 60m.
    Google Calendar FreeBusy is intentionally not asserted or simulated as verified.
    """
    pol=policy(); data=availability(); today=(now or local_now()).astimezone(TZ).date()
    now_utc=(now or local_now()).astimezone(UTC)
    end_offset=pol['booking_horizon_days']
    dates=[today+timedelta(days=i) for i in range(0 if include_today else 1,end_offset+1)]
    if date_filter:
        if date_filter not in dates:return []
        dates=[date_filter]
    users=eligible_users(pol['project_id'])
    occupied={u['id']:_occupied_intervals(u['id'],state,data,today,pol['buffer_after_minutes'],extra_occupied) for u in users}
    needed=timedelta(minutes=pol['visit_duration_minutes']+pol['buffer_after_minutes'])
    increment=timedelta(minutes=pol['slot_search_step_minutes'])
    choices=[]
    for day in dates:
        for user in users:
            intervals=_week_intervals(user['id'],day,data)
            extra,blocked=_exception_intervals(user['id'],day,data,today)
            intervals+=extra
            for begin,end in intervals:
                cursor=begin
                while cursor+needed<=end:
                    candidate_end=cursor+needed
                    if cursor>now_utc and not any(overlaps(cursor,candidate_end,b[0],b[1]) for b in blocked+occupied[user['id']]):
                        local=cursor.astimezone(TZ)
                        choices.append({
                            'advisor_id':user['demo_advisor_id'],'sales_user_id':user['id'],
                            'advisor_name':user['display_name'], 'specialty':user['specialty'],
                            'date':local.date().isoformat(),'start_time':local.strftime('%H:%M'),
                            'starts_at':cursor.isoformat().replace('+00:00','Z'),
                            'visit_end_time':(cursor+timedelta(minutes=pol['visit_duration_minutes'])).astimezone(TZ).strftime('%H:%M'),
                            'buffer_end_time':candidate_end.astimezone(TZ).strftime('%H:%M'),
                            'timezone':pol['timezone'],
                        })
                    cursor+=increment
    choices.sort(key=lambda x:(x['date'],x['start_time'],x['advisor_id']))
    if limit is not None: return choices[:limit]
    return choices


def pick_diverse_slots(choices: list[dict], count: int) -> list[dict]:
    """Prefer distinct advisors for the first offered alternatives when possible."""
    results=[];used=set()
    for slot in choices:
        if slot['sales_user_id'] not in used:
            results.append(slot);used.add(slot['sales_user_id'])
            if len(results)>=count:return results
    for slot in choices:
        if slot not in results:
            results.append(slot)
            if len(results)>=count:return results
    return results
