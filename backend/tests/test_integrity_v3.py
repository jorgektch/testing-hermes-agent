"""Cross-entity integrity checks for normalized synthetic project and scheduling fixtures."""
import json
from pathlib import Path
from datetime import datetime, timezone

from app import domain, scheduling

DATA = Path(__file__).resolve().parents[2]/'shared'/'blackpenguin'
def read(name):return json.loads((DATA/name).read_text(encoding='utf-8'))


def test_canonical_data_links_and_no_duplicate_types():
    project=read('project.json');pid=project['project']['id'];cid=project['company_id']
    types=read('property_types.json')['property_types'];units=read('units.json')['units']
    defs=read('features.json')['feature_definitions']
    assert len(types)==len({t['id'] for t in types})==4
    assert len(units)==len({u['id'] for u in units})==5
    assert all(t['project_id']==pid for t in types)
    assert all(u['project_id']==pid and u['property_type_id'] in {t['id'] for t in types} for u in units)
    assert all(p['project_id']==pid for p in project['phases'])
    assert all(u['phase_id'] in {p['id'] for p in project['phases']} for u in units)
    assert all(t['phase_id'] in {p['id'] for p in project['phases']} for t in types)
    assert all(a['feature_key'] in {f['key'] for f in defs} for a in read('features.json')['project_features'])
    assert all(u['currency']=='USD' and float(u['list_price'])>0 for u in units)
    assert project['project']['company_id']==cid


def test_sales_asset_publication_is_scoped():
    assets=read('assets.json');sources={s['id']:s for s in assets['sources']}
    types={t['id'] for t in read('property_types.json')['property_types']}
    units={u['id'] for u in read('units.json')['units']}
    for a in assets['approved_sales_assets']:
        assert a['source_id'] in sources
        for coverage in a.get('coverage',[]):
            if coverage.get('unit_id'):assert coverage['unit_id'] in units
            if coverage.get('property_type_id'):assert coverage['property_type_id'] in types
    for item in domain.read_catalog()['properties']:
        assert item['blackpenguin_unit_id'] in units
        for path in item['media']['gallery_urls']:
            assert path.startswith('/demo-assets/')
    assert 'bank_financing' not in str(domain.read_catalog())


def test_team_assignments_and_availability_scope():
    team=read('team.json');pol=read('scheduling_policy.json');data=read('availability.json')
    staff={u['id']:u for u in team['users']}
    projects={team['project_id'],*[p['id'] for p in team['related_project_fixtures']]}
    for a in team['project_user_assignments']:
        assert a['user_id'] in staff and a['project_id'] in projects
        assert staff[a['user_id']]['company_id']==team['company_id']
    for w in data['sales_availability_windows']:
        assert w['user_id'] in staff and 0<=w['weekday']<=6
        assert w['start_time']<w['end_time']
    for ex in data['sales_availability_exceptions']:
        assert ex['user_id'] in staff and ex['kind'] in ('unavailable','extra_availability')
    assert pol['project_id']==team['project_id']
    assert pol['visit_duration_minutes']==45 and pol['buffer_after_minutes']==15
    assert pol['slot_search_step_minutes']==60 and pol['offer_expiration_minutes']==30


def test_meeting_fixtures_reference_staff_and_known_project():
    team=read('team.json');meetings=read('meetings.json')['demo_existing_meetings']
    users={u['id'] for u in team['users']}
    valid_projects={team['project_id'],*[p['id'] for p in team['related_project_fixtures']]}
    for m in meetings:
        assert m['assigned_sales_user_id'] in users and m['project_id'] in valid_projects
        assert m['status'] in ('scheduled','confirmed','in_progress')
        assert m['duration_minutes']>0


def _first_pending_meeting(state):
    from datetime import timedelta
    from app import scheduling
    domain.begin_turn(state, 'I want to visit H-201 on Monday.')
    domain.track_property_interest(state, 'H-201', 'interested')
    day = scheduling.local_now().date() + timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    offer = domain.slots(state, day.isoformat())
    assert offer['ok'], offer
    choice = offer['available_slots'][0]
    domain.begin_turn(state, f'I choose {choice["start_time"]} for H-201.')
    assert domain.select_visit_time(state, offer['proposal_id'], choice['date'], choice['start_time'])['ok']
    domain.begin_turn(state, 'Yes, book that offered visit please.')
    result = domain.book_visit(state, 'Jamie', 'Park', 'jamie@example.test', '+51999999991',
                               date_str=choice['date'], start_time=choice['start_time'],
                               proposal_id=offer['proposal_id'], unit_id='H-201')
    assert result['ok'] and result['pending_commit']
    return result, choice


def test_booking_saved_only_after_commit_and_never_external_calendar():
    state = domain.blank_state()
    result, choice = _first_pending_meeting(state)
    meeting = state['bp_episode']['meetings'][0]
    assert meeting['meeting_time'].endswith('Z')
    assert datetime.fromisoformat(meeting['meeting_time'].replace('Z','+00:00')).tzinfo == timezone.utc
    assert meeting['gcal_event_id'] is None
    assert meeting['status'] == 'pending_assignment'
    assert meeting['assigned_sales_user_id'] is None
    assert state['bp_episode']['sales_booking_operations'][0]['calendar_status'] == 'simulation'
    assert domain.blank_state()['bp_episode']['meetings'] == []


def test_cancellation_is_evidenced_and_preserves_operation_history():
    state = domain.blank_state()
    result, choice = _first_pending_meeting(state)
    mid = state['bp_episode']['meetings'][0]['id']
    # The real persistence transition to confirmed is covered in v8 DB tests;
    # this legacy domain-unit test starts from a committed fixture state.
    state['bp_episode']['meetings'][0]['status'] = 'confirmed'
    state['booked_visits'][0]['status'] = 'confirmed'
    domain.begin_turn(state, 'I want to cancel my visit, please.')
    response = domain.cancel_visit(state, mid, reason='Changed my plans')
    assert response['ok']
    assert state['bp_episode']['meetings'][0]['status'] == 'cancelled'
    op = state['bp_episode']['sales_cancellation_operations'][0]
    assert op['meeting_id'] == mid
    assert op['reason_message_id'] == state['_active_source_message_id']
    assert op['calendar_status'] == 'simulation'
    assert 'error' in domain.cancel_visit(state, mid)


def test_reschedule_preserves_original_and_creates_pending_replacement():
    from datetime import timedelta
    from app import scheduling
    state = domain.blank_state()
    result, choice = _first_pending_meeting(state)
    mid = state['bp_episode']['meetings'][0]['id']
    state['bp_episode']['meetings'][0]['status'] = 'confirmed'
    state['booked_visits'][0]['status'] = 'confirmed'
    day = scheduling.local_now().date() + timedelta(days=2)
    if day.weekday() == 6:
        day += timedelta(days=1)
    fresh = domain.slots(state, day.isoformat())
    assert fresh['ok'], fresh
    next_slot = fresh['available_slots'][0]
    domain.begin_turn(state, f'I choose {next_slot["start_time"]} to reschedule H-201.')
    assert domain.select_visit_time(state, fresh['proposal_id'], next_slot['date'], next_slot['start_time'])['ok']
    domain.begin_turn(state, 'Yes, please reschedule the H-201 visit to that offered hour.')
    res = domain.reschedule_visit(state, mid, 'Jamie', 'Park', 'jamie@example.test', '+51999999991',
                                  date_str=next_slot['date'], start_time=next_slot['start_time'],
                                  proposal_id=fresh['proposal_id'], unit_id='H-201')
    assert res['ok'], res
    original, replacement = state['bp_episode']['meetings']
    assert original['id'] == mid and original['status'] == 'cancelled'
    assert original['confirmation_status'] == 'rescheduled'
    assert replacement['status'] == 'pending_assignment' and replacement['id'] != mid
    assert len(state['bp_episode']['sales_booking_operations']) == 2
    op = state['bp_episode']['sales_reschedule_operations'][0]
    assert op['original_meeting_id'] == mid
    assert op['replacement_meeting_id'] == replacement['id']
    assert op['calendar_status'] == 'simulation'


def test_typed_feature_search_respects_model_and_inventory():
    state=domain.blank_state()
    answer=domain.evaluate_properties(state,400000,'within 5 months',3,'House',
                                       {'kitchen_layout':'open','private_backyard':True})
    assert {'H-204','H-101','H-104'} == {x['unit_id'] for x in answer['matching_properties']}
    assert 'H-102' not in json.dumps(answer)
    assert 'error' in domain.evaluate_properties(state,400000,preferred_property_type='Condo')
    assert 'error' in domain.evaluate_properties(state,400000,required_features={'private_pool':True})
