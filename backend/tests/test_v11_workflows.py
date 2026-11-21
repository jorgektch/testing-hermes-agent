"""Hermes v11 regression cases: revisable preferences, assets and booking safety."""
from datetime import timedelta

from app import domain, sales_preferences, scheduling, delivery_policy


def declared(state, text):
    domain.begin_turn(state,text)
    result=domain.verify_explicit_preferences(state,text)
    assert result.get('ok'),result
    return state['search_preferences']


def available_days():
    today=scheduling.local_now().date()
    options=[]
    for day_offset in range(1,15):
        day=today+timedelta(days=day_offset)
        if scheduling.available_slots(domain.blank_state(),date_filter=day):
            options.append(day)
        if len(options)>=2:
            return options
    raise AssertionError('Synthetic calendar has fewer than two days available')


def test_two_bedroom_request_is_a_preference_not_a_minimum():
    state=domain.blank_state()
    pref=declared(state,'I am looking for a 2-bedroom house')
    assert pref['bedrooms']['mode']=='preferred'
    assert pref['bedrooms']['preferred']==2
    assert pref['bedrooms']['minimum'] is None
    declared(state,"I don't want to spend more than $300k")
    result=domain.evaluate_properties(state)
    assert [x['unit_id'] for x in result['matching_properties']]==['H-201']
    assert not result['optional_alternatives']
    assert result['applied_criteria']['budget']['maximum_usd']==300000


def test_optional_alternative_is_labeled_and_only_on_request():
    state=domain.blank_state()
    declared(state,'I am looking for a 2-bedroom house')
    declared(state,"I don't want to spend more than $300k")
    domain.begin_turn(state,'Any other options or alternatives?')
    result=domain.evaluate_properties(state)
    assert [x['unit_id'] for x in result['matching_properties']]==['H-201']
    assert [x['unit_id'] for x in result['optional_alternatives']]==['H-204']
    assert 'Different bedroom count' in result['optional_alternatives'][0]['mismatch_reason']


def test_budget_replacement_supersedes_cap_and_recomputes_matches():
    state=domain.blank_state()
    declared(state,"I don't want to spend more than $300k")
    assert state['lead']['budget_usd']==300000
    prefs=declared(state,'Actually I can go up to $320k')
    assert prefs['budget']['maximum_usd']==320000
    assert state['lead']['budget_usd']==320000
    assert len(state['search_preferences_history'])==2
    result=domain.evaluate_properties(state)
    assert 'H-101' in {x['unit_id'] for x in result['matching_properties']}


def test_budget_range_minimum_maximum_and_target_are_distinct():
    assert sales_preferences.parse_declarations('My budget is between $250k and $300k')['budget']=={
        'mode':'range','minimum_usd':250000,'maximum_usd':300000}
    assert sales_preferences.parse_declarations('My budget is around $280k')['budget']=={
        'mode':'target','target_usd':280000}
    assert sales_preferences.parse_declarations('I have exactly $270k available')['budget']=={
        'mode':'fixed_funds','target_usd':270000,'maximum_usd':270000}
    assert sales_preferences.parse_declarations('I only want a listing price exactly $270k')['budget']=={
        'mode':'exact_price','exact_price_usd':270000}


def test_exact_and_minimum_bedrooms_are_distinct():
    state=domain.blank_state()
    declared(state,'I need exactly 2 bedrooms')
    assert [x['unit_id'] for x in domain.evaluate_properties(state)['matching_properties']]==['H-201']
    declared(state,'Actually I need at least 3 bedrooms')
    matches={x['unit_id'] for x in domain.evaluate_properties(state)['matching_properties']}
    assert 'H-201' not in matches
    assert {'H-204','H-101'}.issubset(matches)


def test_asset_discovery_does_not_return_unsolicited_urls():
    state=domain.blank_state()
    domain.begin_turn(state,'Do you have floor plans for H-201?')
    result=domain.list_unit_assets(state,'H-201')
    assert result['ok']
    assert result['gallery']['available']
    assert result['gallery']['image_count']==2
    assert any(doc['type']=='brochure' for doc in result['documents'])
    assert not any(doc['type']=='floor_plan' for doc in result['documents'])
    assert 'https://' not in str(result)
    assert all('url' not in d for d in result['documents'])


def test_media_history_only_marks_links_really_sent():
    state=domain.blank_state()
    media=domain.unit_media('H-201')
    url=media['gallery_url']
    state['_approved_media_urls_this_turn']=[url]
    delivery_policy.validate_outbound('Here are the photos: '+url,state)
    assert state['shared_media']['H-201']['gallery_sent'] is True
    domain.begin_turn(state,'What about its PDF?')
    assets=domain.list_unit_assets(state,'H-201')
    assert assets['gallery']['already_shared']
    assert not assets['documents'][0]['already_shared']


def test_calendar_browsing_does_not_erase_selected_time():
    first,second=available_days()
    state=domain.blank_state()
    domain.begin_turn(state,'I want to visit H-201.')
    offer=domain.slots(state,first.isoformat())
    assert offer['ok'],offer
    hour=offer['available_slots'][0]
    domain.begin_turn(state,f'I choose {hour["start_time"]} on {first.isoformat()}.')
    selected=domain.select_visit_time(state,offer['proposal_id'],hour['date'],hour['start_time'])
    assert selected['ok'],selected
    previous=state['visit_draft'].copy()
    response=domain.slots(state,second.isoformat())
    assert response['ok'],response
    assert state['visit_draft']==previous
    assert state['booking_proposal']['proposal_id']==offer['proposal_id']
    assert state['last_offered_date']==second.isoformat()


def test_select_3pm_from_last_offered_day_not_prior_day(monkeypatch):
    first,second=available_days()
    state=domain.blank_state()
    domain.begin_turn(state,'I want to visit H-201')
    def fake(_state, date_filter=None, **kwargs):
        day=date_filter.isoformat()
        return [{'date':day,'start_time':'15:00','timezone':'America/Lima','sales_user_id':'advisor-x'}]
    monkeypatch.setattr(scheduling,'available_slots',fake)
    old=domain.slots(state,first.isoformat())
    new=domain.slots(state,second.isoformat())
    assert old['ok'] and new['ok']
    domain.begin_turn(state,'3pm its ok')
    selected=domain.capture_explicit_visit_time(state,'3pm its ok')
    assert selected['captured'],selected
    assert state['visit_draft']['date']==second.isoformat()
    assert state['visit_draft']['start_time']=='15:00'
    assert state['visit_draft']['proposal_id']==new['proposal_id']


def test_short_ok_is_not_booking_consent_and_clear_confirmation_is():
    state=domain.blank_state()
    first=available_days()[0]
    domain.begin_turn(state,'I want to visit H-201')
    offer=domain.slots(state,first.isoformat())
    choice=offer['available_slots'][0]
    domain.begin_turn(state,f'I choose {choice["start_time"]} on {first.isoformat()}')
    assert domain.select_visit_time(state,offer['proposal_id'],first.isoformat(),choice['start_time'])['ok']
    domain.end_turn(state,'For H-201 on that date and time, please reply "Yes, I confirm" to book. Confirm?')
    assert state['confirmation_request']
    domain.begin_turn(state,'Ok')
    assert not domain.ready_to_finalize_visit(state)
    domain.begin_turn(state,'Yes, I confirm')
    assert domain.ready_to_finalize_visit(state)


def test_false_confirmation_is_rewritten_from_server_state():
    state=domain.blank_state()
    state['visit_draft']={'unit_id':'H-201','date':'2026-11-25',
                          'start_time':'15:00','status':'awaiting_confirmation'}
    result=delivery_policy.validate_outbound(
        'Your visit to H-201 on November 24 is confirmed!',state)
    assert 'not booked yet' in result
    assert '2026-11-25' in result
    assert 'November 24' not in result


def test_pending_draft_not_switched_when_other_property_discussed():
    state=domain.blank_state()
    domain.begin_turn(state,'I want to visit H-201')
    assert state['visit_draft']['unit_id']=='H-201'
    domain.begin_turn(state,'What is the price of H-204?')
    assert state['conversation_focus']['unit_id']=='H-204'
    assert state['visit_draft']['unit_id']=='H-201'
