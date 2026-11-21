"""Regression scenarios for Hermes v10 contextual focus and appointment handling.

All scenarios use the synthetic catalog and local scheduling fixtures.
"""
from datetime import timedelta

from app import domain, scheduling


def next_open_day():
    today = scheduling.local_now().date()
    for delta in range(1, scheduling.policy()['booking_horizon_days'] + 1):
        candidate = today + timedelta(days=delta)
        if scheduling.available_slots(domain.blank_state(), date_filter=candidate):
            return candidate
    raise AssertionError('No free synthetic date exists in the appointment horizon')


def next_open_day_without_check():
    return scheduling.local_now().date() + timedelta(days=2)


def test_focus_tracks_house_on_question_without_visit_selection():
    state = domain.blank_state()
    domain.begin_turn(state, 'How much is H-201?')
    assert state['conversation_focus']['unit_id'] == 'H-201'
    assert state['exploring_unit_id'] == 'H-201'
    assert state['visit_draft']['unit_id'] is None
    assert state['lead']['interest_unit_id'] is None
    unit = domain._units_by_code()['H-201']
    bp = state['bp_episode']
    dialogue = bp['sales_dialogue_states']['state_json']
    assert dialogue['selected_unit_id'] == unit['blackpenguin_unit_id']
    assert dialogue['selected_property_type_id'] == unit['property_type_id']


def test_implicit_visit_uses_focus_without_reasking():
    state = domain.blank_state()
    domain.begin_turn(state, 'Tell me about H-201.')
    domain.end_turn(state, 'The H-201 has 2 bedrooms. Want to visit?')
    domain.begin_turn(state, 'Yes, I want to visit it next week.')
    assert state['visit_draft']['unit_id'] == 'H-201'
    assert state['lead']['interest_unit_id'] is None
    result = domain.slots(state, next_open_day().isoformat())
    assert result['ok'] and result['available_slots'], result
    assert result['unit_id'] == 'H-201'
    assert not any('advisor' in str(item) for item in result['available_slots'])


def test_question_about_another_house_preserves_visit_scope():
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201.')
    offer = domain.slots(state, next_open_day().isoformat())
    assert offer['ok']
    previous_id = offer['proposal_id']
    domain.end_turn(state, 'For H-201, which hour works?')
    domain.begin_turn(state, 'What is the price of H-204?')
    assert state['conversation_focus']['unit_id'] == 'H-204'
    assert state['visit_draft']['unit_id'] == 'H-201'
    assert state['booking_proposal']['proposal_id'] == previous_id


def test_explicit_new_visit_invalidates_previous_offer():
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201.')
    assert domain.slots(state, next_open_day().isoformat())['ok']
    domain.end_turn(state, 'Choose a time for H-201.')
    domain.begin_turn(state, 'Actually I want to visit H-204 instead.')
    assert state['conversation_focus']['unit_id'] == 'H-204'
    assert state['visit_draft']['unit_id'] == 'H-204'
    assert state['booking_proposal'] is None
    assert state['visit_draft']['start_time'] is None


def test_no_slots_returns_actionable_error_and_caches_repeat(monkeypatch):
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201.')
    monkeypatch.setattr(scheduling, 'available_slots', lambda *a, **kw: [])
    result = domain.slots(state, next_open_day_without_check().isoformat())
    assert result['error_code'] == 'NO_SLOTS'
    assert result['unit_id'] == 'H-201'
    assert result['next_action']
    assert state['booking_proposal'] is None
    repeat = domain.slots(state, result['date'])
    assert repeat['error_code'] == 'NO_SLOTS' and repeat['already_checked']


def test_calendar_error_does_not_invent_available_hours(monkeypatch):
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201.')
    def outage(*args, **kwargs):
        raise ConnectionError('synthetic outage should not be exposed')
    monkeypatch.setattr(scheduling, 'available_slots', outage)
    result = domain.slots(state, next_open_day_without_check().isoformat())
    assert result['error_code'] == 'CALENDAR_UNAVAILABLE'
    assert 'synthetic outage' not in str(result)


def test_outside_horizon_distinct_from_no_slots():
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201.')
    future = scheduling.local_now().date() + timedelta(days=100)
    result = domain.slots(state, future.isoformat())
    assert result['error_code'] == 'DATE_OUTSIDE_HORIZON'
    assert result['allowed_start'] and result['allowed_end']


def test_week_query_proposes_dates_without_reserving_hours():
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201 next week in the morning.')
    result = domain.slots(state, 'next week', time_preference='morning')
    assert result.get('ok') or result['error_code'] in ('NO_SLOTS', 'DATE_OUTSIDE_HORIZON')
    if result.get('ok'):
        assert result['unit_id'] == 'H-201'
        assert result['available_dates']
        assert result['proposal_id'] is None
        assert state['booking_proposal'] is None
        assert all(int(day['first_start'][:2]) < 12 for day in result['available_dates'])


def test_context_change_does_not_reassign_booking_unit():
    state = domain.blank_state()
    domain.begin_turn(state, 'I want to visit H-201.')
    offer = domain.slots(state, next_open_day().isoformat())
    hour = offer['available_slots'][0]
    domain.end_turn(state, 'Which hour for H-201?')
    domain.begin_turn(state, f'I choose {hour["start_time"]} for H-201.')
    selected = domain.select_visit_time(
        state, offer['proposal_id'], hour['date'], hour['start_time'])
    assert selected['ok'], selected
    domain.end_turn(state, 'I can schedule H-201 on that day and time. Confirm?')
    domain.begin_turn(state, 'What is the area of H-204?')
    assert state['conversation_focus']['unit_id'] == 'H-204'
    assert state['visit_draft']['unit_id'] == 'H-201'
    domain.end_turn(state, 'H-204 measures 135 m2. Your H-201 visit is still pending.')
    domain.begin_turn(state, 'Yes, confirm the visit we discussed.')
    booking = domain.book_visit(
        state, 'Test', 'Lead', 'test@example.com', '+51999999999',
        date_str=hour['date'], start_time=hour['start_time'],
        unit_id='H-201', proposal_id=offer['proposal_id'])
    assert booking.get('ok') and booking['unit_id'] == 'H-201', booking


def test_unit_maps_are_truthfully_city_scoped():
    for unit in domain.read_catalog()['properties']:
        location = unit['location']
        assert location['verification_status'] == 'synthetic'
        assert location['map_scope'] == 'city_reference_only'
        assert location['city'] == 'Lima'
        assert location['maps_url'].startswith('https://www.google.com/maps/search/?api=1&query=')
        assert 'exact' in location['note'].lower()


def test_yes_to_single_visit_invitation_preserves_context():
    state = domain.blank_state()
    domain.begin_turn(state, 'Tell me about H-201.')
    domain.end_turn(state, 'Would you like to visit H-201?')
    assert state['pending_visit_offer']['unit_id'] == 'H-201'
    domain.begin_turn(state, 'Yes please.')
    assert state['visit_draft']['unit_id'] == 'H-201'


def test_month_named_date_without_reasking():
    day = next_open_day()
    label = day.strftime('%B') + ' ' + str(day.day) + 'th'
    assert domain.date_input(label) == day.isoformat()
