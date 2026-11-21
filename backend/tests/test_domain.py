from app import domain


def test_catalog_contains_one_project_four_available_homes():
    catalog = domain.read_catalog()
    assert catalog['project_info']['project_id'] == '22222222-2222-4222-8222-222222222222'
    assert catalog['project_info']['name'] == 'Residencial Jardines del Sol'
    assert len(catalog['properties']) == 4
    assert {u['property_type'] for u in catalog['properties']} == {'House'}
    assert {u['unit_id'] for u in catalog['properties']} == {'H-201','H-204','H-101','H-104'}
    assert 'H-102' not in {u['unit_id'] for u in catalog['properties']}


def test_media_combinations():
    data = {x['unit_id']: x for x in domain.read_catalog()['properties']}
    assert data['H-201']['media']['gallery_urls'] and data['H-201']['media']['floorplan_brochure_pdf']
    assert data['H-204']['media']['gallery_urls'] and data['H-204']['media']['floorplan_brochure_pdf']
    assert data['H-101']['media']['gallery_urls'] and data['H-101']['media']['floorplan_brochure_pdf']
    assert data['H-104']['media']['gallery_urls'] and data['H-104']['media']['floorplan_brochure_pdf']


def test_match_budget_and_bedrooms():
    state = domain.blank_state()
    result = domain.evaluate_properties(state, 260000, 'within 2 months', 2)
    assert [p['unit_id'] for p in result['matching_properties']] == ['H-201']
    assert all(x['price_usd'] > 260000 for x in result['out_of_budget_properties'])
    assert state['shortlisted_unit_ids'] == ['H-201']


def test_lead_progress_and_validation():
    state = domain.blank_state()
    domain.begin_turn(state, 'I can spend USD 260000, and I want to buy within 2 months.')
    domain.update_lead(state, budget_usd=260000, purchase_timeframe='within 2 months')
    domain.end_turn(state, 'Let me recommend a fictional apartment.')
    assert state['stage'] == 'Qualified lead'
    assert 'error' in domain.update_lead(state, email='this-is-not-an-email')
    assert state['lead']['email'] is None
    facts=state['bp_episode']['leads']['lead_profile_data']['sales_profile']['current']
    assert facts['budget']['value'] == 'USD 260000'
    assert facts['budget']['evidence'].startswith('I can spend')
    assert facts['budget']['source_ref']['message_id'] == state['bp_episode']['sales_messages'][0]['id']
    assert state['bp_episode']['sales_conversations']['channel'] == 'simulation'


def test_available_slots_and_single_booking():
    state = domain.blank_state()
    offer = domain.slots(state)
    available = offer['available_slots']
    assert 1 <= len(available) <= 3
    first = available[0]
    # Searching does not reserve the appointment or create Meeting.
    assert state['bp_episode']['meetings'] == []
    assert offer['proposal_id'] == state['booking_proposal']['proposal_id']
    unconfirmed = domain.book_visit(state, 'Alice', 'Morgan', 'alice@example.org', '+51999999999',
                                    first['advisor_id'], first['date'], first['start_time'],
                                    offer['proposal_id'], unit_id='H-201')
    assert 'error' in unconfirmed
    domain.begin_turn(state, 'Yes, please confirm that visit for me.')
    res = domain.book_visit(state, 'Alice', 'Morgan', 'alice@example.org', '+51999999999',
                            first['advisor_id'], first['date'], first['start_time'],
                            offer['proposal_id'], unit_id='H-201')
    assert res['ok'] is True
    assert res['booking']['visit_duration_minutes'] == 45
    assert res['booking']['buffer_minutes'] == 15
    assert res['booking']['simulation_only'] is True
    assert len(state['bp_episode']['meetings']) == 1
    meeting=state['bp_episode']['meetings'][0]
    assert meeting['lead_id'] == state['bp_episode']['ids']['lead_id']
    assert meeting['status']=='confirmed'
    assert meeting['calendar_sync_status']=='simulation_ready'
    assert meeting['meeting_time'].endswith('Z')
    assert len(state['bp_episode']['sales_booking_operations']) == 1
    assert not any(s['advisor_id'] == first['advisor_id'] and s['date'] == first['date']
                   and s['start_time'] == first['start_time']
                   for s in domain.slots(state)['available_slots'])
    again = domain.book_visit(state, 'Alice', 'Morgan', 'alice@example.org', '+51999999999',
                              first['advisor_id'], first['date'], first['start_time'],
                              offer['proposal_id'], unit_id='H-201')
    assert 'error' in again


def test_proposals_allow_general_project_visits():
    state=domain.blank_state()
    proposed=domain.slots(state)
    choice=proposed['available_slots'][0]
    domain.begin_turn(state, 'Yes, please book a visit to the project.')
    result=domain.book_visit(state, 'Alice','Morgan','alice@example.org','+51999999999',
                             choice['advisor_id'],choice['date'],choice['start_time'],proposed['proposal_id'])
    assert result['ok']
    assert state['bp_episode']['meetings'][0]['visit_unit_id'] is None
    assert state['bp_episode']['meetings'][0]['visit_context']['property_association']=='unknown'


def test_offer_rejects_expired_or_wrong_proposal():
    state=domain.blank_state()
    proposal=domain.slots(state)
    opt=proposal['available_slots'][0]
    domain.begin_turn(state,'Yes, confirm the appointment.')
    kwargs=dict(first_name='Alice',last_name='Morgan',email='alice@example.org',
                phone='+51999999999',advisor_id=opt['advisor_id'],
                date_str=opt['date'],start_time=opt['start_time'],unit_id='H-201')
    assert 'error' in domain.book_visit(state,proposal_id='fake',**kwargs)
    state['booking_proposal']['expires_at']='2000-01-01T00:00:00+00:00'
    assert 'expired' in domain.book_visit(state,proposal_id=proposal['proposal_id'],**kwargs)['error']


def test_slot_spacing_is_half_hour_and_buffers_are_sixty_minutes():
    from app import scheduling
    state=domain.blank_state()
    all_slots=scheduling.available_slots(state)
    assert all_slots
    assert all(s['start_time'].endswith((':00',':30')) for s in all_slots)
    assert all(s['buffer_end_time']!=s['visit_end_time'] for s in all_slots)
    assert scheduling.policy()['visit_duration_minutes']==45
    assert scheduling.policy()['slot_search_step_minutes']==30


def test_global_cross_project_busy_and_exceptions():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app import scheduling
    # Fixed Friday ensures day_offset=1 is Saturday and +3 is Monday.
    now=datetime(2026,11,20,8,0,tzinfo=ZoneInfo('America/Lima'))
    state=domain.blank_state()
    slots=scheduling.available_slots(state,now=now)
    saturday='2026-11-21'; monday='2026-11-23'
    olivia=scheduling.team()['users'][0]['demo_advisor_id']
    sophia=scheduling.team()['users'][2]['demo_advisor_id']
    # Saturday 10:00-11:00 is blocked by a project-A 45m+15m visit.
    assert not any(s['date']==saturday and s['start_time']=='10:00' and s['advisor_id']==olivia for s in slots)
    # Monday 11:00 is blocked for Sophia by a visit to a different project.
    assert not any(s['date']==monday and s['start_time']=='11:00' and s['advisor_id']==sophia for s in slots)
    # Explicit unavailable exception overrides Olivia's regular Monday 09-12 window.
    assert not any(s['date']==monday and s['start_time']=='10:00' and s['advisor_id']==olivia for s in slots)

def test_booking_does_not_store_financing():
    catalog = domain.read_catalog()
    assert 'financing' not in str(catalog).lower()
