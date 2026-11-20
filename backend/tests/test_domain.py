from app import domain


def test_catalog_contains_one_project_four_homes():
    catalog = domain.read_catalog()
    assert catalog['project_info']['project_id'] == 'EVR-001'
    assert len(catalog['properties']) == 4
    assert {u['property_type'] for u in catalog['properties']} == {'Apartment', 'House'}


def test_media_combinations():
    data = {x['unit_id']: x for x in domain.read_catalog()['properties']}
    assert data['EV-A101']['media']['gallery_urls'] and data['EV-A101']['media']['floorplan_brochure_pdf']
    assert data['EV-A204']['media']['gallery_urls'] and data['EV-A204']['media']['floorplan_brochure_pdf'] is None
    assert not data['EV-H03']['media']['gallery_urls'] and data['EV-H03']['media']['floorplan_brochure_pdf']
    assert data['EV-H08']['media']['gallery_urls'] and data['EV-H08']['media']['floorplan_brochure_pdf']


def test_match_budget_and_bedrooms():
    state = domain.blank_state()
    result = domain.evaluate_properties(state, 175000, 'within 2 months', 2)
    assert [p['unit_id'] for p in result['matching_properties']] == ['EV-A101']
    assert all(x['price_usd'] > 175000 for x in result['out_of_budget_properties'])
    assert state['shortlisted_unit_ids'] == ['EV-A101']


def test_lead_progress_and_validation():
    state = domain.blank_state()
    domain.update_lead(state, budget_usd=175000, purchase_timeframe='within 2 months')
    assert state['stage'] == 'Qualified lead'
    assert 'error' in domain.update_lead(state, email='this-is-not-an-email')
    assert state['lead']['email'] is None


def test_available_slots_and_single_booking():
    state = domain.blank_state()
    available = domain.slots(state)['available_slots']
    assert available
    first = available[0]
    res = domain.book_visit(state, 'Alice', 'Morgan', 'alice@example.org', '+51999999999',
                            'EV-A101', first['advisor_id'], first['date'], first['start_time'])
    assert res['ok'] is True
    assert res['booking']['visit_duration_minutes'] == 45
    assert res['booking']['simulation_only'] is True
    assert not any(
        s['advisor_id'] == first['advisor_id'] and s['date'] == first['date'] and s['start_time'] == first['start_time']
        for s in domain.slots(state)['available_slots']
    )
    again = domain.book_visit(state, 'Alice', 'Morgan', 'alice@example.org', '+51999999999',
                              'EV-A101', first['advisor_id'], first['date'], first['start_time'])
    assert 'error' in again


def test_slot_spacing_is_hourly():
    state = domain.blank_state()
    all_slots = domain.slots(state)['available_slots']
    for s in all_slots:
        assert s['start_time'].endswith(':00')
        assert s['buffer_end_time'].endswith(':00')


def test_booking_does_not_store_financing():
    catalog = domain.read_catalog()
    assert 'financing' not in str(catalog).lower()
