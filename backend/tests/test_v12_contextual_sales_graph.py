"""V12 regression suite from anonymized Nov 21 Hermes conversation.

No private customer contacts or real inventory are embedded in these tests.
They enforce public-fact readability, non-linear browsing, strict booking
separation, and actual sent-message memory.
"""
from app import conversation_context, delivery_policy, domain, property_filters


def start_with_single_h201():
    state=domain.blank_state()
    domain.begin_turn(state,'I am looking for a 2-bedroom house')
    assert domain.verify_explicit_preferences(state,
        'I am looking for a 2-bedroom house')['ok']
    domain.end_turn(state,'Which budget range works for your two-bedroom house?')
    domain.begin_turn(state,"I don't want to spend more than $300k")
    assert domain.verify_explicit_preferences(state,
        "I don't want to spend more than $300k")['ok']
    search=domain.evaluate_properties(state)
    assert [x['unit_id'] for x in search['matching_properties']]==['H-201']
    domain.end_turn(state,'H-201 is a two-bedroom garden house at $245,000. Would you like to explore its details?')
    return state


def test_area_question_after_single_offer_does_not_require_interest():
    state=start_with_single_h201()
    assert state['conversation_focus']['unit_id'] is None
    domain.begin_turn(state,'What is the area?')
    assert state['_turn_property_resolution']['reason']=='single_recent_option'
    assert state['conversation_focus']['unit_id']=='H-201'
    assert state['lead']['interest_unit_id'] is None
    assert state['visit_draft']['unit_id'] is None
    details=domain.unit_details(state)
    assert details['ok'] and details['area_sqm']==110
    assert domain.track_property_interest(state,'H-201','discussed')['ok']
    assert state['lead']['interest_unit_id'] is None


def test_legacy_record_single_shortlist_recovers_missing_focus():
    state=domain.blank_state()
    state['shortlisted_unit_ids']=['H-201']
    state['conversation_focus']['unit_id']=None
    domain.begin_turn(state,'What is the square footage?')
    assert state['conversation_focus']['unit_id']=='H-201'
    assert domain.unit_details(state)['area_sqm']==110


def test_verified_area_repair_breaks_repeated_confirmation_loop():
    state=start_with_single_h201()
    domain.begin_turn(state,'What is the square footage?')
    state['_details_for_current_turn']=domain.unit_details(state)
    reply=delivery_policy.validate_outbound(
        'To provide the square footage, please confirm your interest in H-201 again.',state)
    assert '1,184 sq ft' in reply
    assert '1,561 sq ft' in reply
    assert 'confirm' not in reply.lower()
    domain.end_turn(state,reply)
    assert state['answered_topics'][-1]['topic']=='area'
    assert state['answered_topics'][-1]['unit_id']=='H-201'
    assert state['bp_episode']['sales_dialogue_states']['state_json']['memory']['answered_topics']


def test_repeated_yes_to_details_is_not_booking_consent():
    state=start_with_single_h201()
    domain.begin_turn(state,'Yes')
    assert state['conversation_focus']['unit_id']=='H-201'
    assert state['visit_draft']['status']=='idle'
    assert domain.unit_details(state)['ok']
    assert not state.get('confirmation_request')


def test_explicit_interest_in_that_property_is_resolved_once():
    state=start_with_single_h201()
    domain.begin_turn(state,'Yes, I am interested in that property')
    assert state['conversation_focus']['unit_id']=='H-201'
    result=domain.track_property_interest(state,'H-201','interested')
    assert result['ok'] and state['lead']['interest_unit_id']=='H-201'
    assert state['visit_draft']['unit_id'] is None


def test_multiple_recent_options_need_real_clarification():
    state=domain.blank_state()
    domain.begin_turn(state,'What available houses are there?')
    domain.end_turn(state,'You can review H-201 or H-204. Which would you prefer?')
    domain.begin_turn(state,'What is the area?')
    assert state['_turn_property_resolution']['ambiguous']
    result=domain.unit_details(state,'H-201')
    assert result['error_code']=='REFERENCE_AMBIGUOUS'
    assert state['conversation_focus']['unit_id'] is None


def test_ordinal_second_option_selects_correct_topic():
    state=domain.blank_state()
    domain.begin_turn(state,'What are my options?')
    domain.end_turn(state,'H-201 and H-204 are options.')
    domain.begin_turn(state,'What is the area of the second one?')
    assert state['conversation_focus']['unit_id']=='H-204'
    assert domain.unit_details(state)['area_sqm']==135
    assert state['visit_draft']['unit_id'] is None


def test_comparative_cheapest_of_two_options_is_unambiguous():
    state=domain.blank_state()
    domain.begin_turn(state,'What are my options?')
    domain.end_turn(state,'H-201 and H-204 are available.')
    domain.begin_turn(state,'Which is the cheapest?')
    assert state['_turn_property_resolution']['unit_id']=='H-201'


def test_ask_about_b_preserves_a_visit_and_return_to_a():
    state=domain.blank_state()
    domain.begin_turn(state,'I want to visit H-201')
    assert state['visit_draft']['unit_id']=='H-201'
    domain.end_turn(state,'For H-201, what day would you like to visit?')
    domain.begin_turn(state,'What is the kitchen like in H-204?')
    assert state['conversation_focus']['unit_id']=='H-204'
    assert state['visit_draft']['unit_id']=='H-201'
    assert domain.unit_details(state)['confirmed_features']['kitchen_layout']=='open'
    domain.end_turn(state,'H-204 has an open kitchen.')
    domain.begin_turn(state,'Back to H-201, how big is it?')
    assert state['conversation_focus']['unit_id']=='H-201'
    assert state['visit_draft']['unit_id']=='H-201'


def test_looking_for_larger_area_is_a_refinement_not_confirmation():
    state=start_with_single_h201()
    domain.begin_turn(state,'I want something with more area')
    domain.verify_explicit_preferences(state,'I want something with more area')
    minimum=state['property_filters']['min_area_sqm']
    assert 110<minimum<111
    assert state['lead']['interest_unit_id'] is None
    result=domain.evaluate_properties(state)
    assert not result['matching_properties']
    assert [x['unit_id'] for x in result['optional_alternatives']]==['H-204']


def test_relative_cheaper_price_does_not_overwrite_declared_budget():
    state=start_with_single_h201()
    domain.begin_turn(state,'Do you have anything cheaper?')
    domain.verify_explicit_preferences(state,'Do you have anything cheaper?')
    filters=state['property_filters']
    assert filters['relative_price_lt']==245000
    assert state['search_preferences']['budget']['maximum_usd']==300000
    assert not domain.evaluate_properties(state)['matching_properties']


def test_new_explicit_budget_can_supersede_relative_price_filter():
    state=start_with_single_h201()
    domain.begin_turn(state,'Anything cheaper?')
    domain.verify_explicit_preferences(state,'Anything cheaper?')
    domain.end_turn(state,'No matching house below the H-201 asking price.')
    domain.begin_turn(state,'Actually I can go up to $320k')
    domain.verify_explicit_preferences(state,'Actually I can go up to $320k')
    assert state['search_preferences']['budget']['maximum_usd']==320000
    assert state['property_filters']['relative_price_lt'] is None


def test_multiple_filters_in_one_message_are_applied_together():
    state=domain.blank_state()
    domain.begin_turn(state,'I need at least 2 bathrooms, at least 120 sq m, with an open kitchen')
    domain.verify_explicit_preferences(state,
        'I need at least 2 bathrooms, at least 120 sq m, with an open kitchen')
    f=state['property_filters']
    assert f['min_bathrooms']==2
    assert f['min_area_sqm']==120
    assert f['kitchen_layout']=='open'
    results=domain.evaluate_properties(state)
    assert 'H-201' not in {u['unit_id'] for u in results['matching_properties']}
    assert 'H-204' in {u['unit_id'] for u in results['matching_properties']}


def test_sent_gallery_history_supports_truthful_resend():
    state=domain.blank_state()
    domain.begin_turn(state,'Please show the H-201 gallery')
    media=domain.unit_media('H-201')
    url=media['gallery_url']
    state['_approved_media_urls_this_turn']=[url]
    first=delivery_policy.validate_outbound('Gallery for H-201: '+url,state)
    assert first.count(url)==1
    domain.end_turn(state,first)
    assert state['shared_media']['H-201']['gallery_sent']
    domain.begin_turn(state,'Can you send the photos again?')
    state['_approved_media_urls_this_turn']=[url]
    state['_media_resend_urls_this_turn']=[url]
    again=delivery_policy.validate_outbound('Gallery: '+url,state)
    assert 'resending' in again.lower()
    assert again.count(url)==1


def test_public_details_cannot_read_different_house_by_mistake():
    state=start_with_single_h201()
    domain.begin_turn(state,'How many bathrooms does it have?')
    wrong=domain.unit_details(state,'H-204')
    assert wrong['error_code']=='PROPERTY_NOT_IN_CONTEXT'
    assert wrong['resolved_unit_id']=='H-201'
    assert domain.unit_details(state)['bathrooms']==2


def test_no_property_topic_access_when_multiple_are_ambiguous():
    state=domain.blank_state()
    domain.begin_turn(state,'Tell me about the project')
    domain.end_turn(state,'H-201 or H-204 are two options.')
    domain.begin_turn(state,'Show photos')
    assert domain.list_unit_assets(state)['error_code']=='REFERENCE_UNRESOLVED'


def test_preference_and_visit_are_independent_state_dimensions():
    state=start_with_single_h201()
    domain.begin_turn(state,'I want to visit it')
    assert state['visit_draft']['unit_id']=='H-201'
    domain.end_turn(state,'Which day for H-201?')
    domain.begin_turn(state,'What about H-204?')
    assert state['conversation_focus']['unit_id']=='H-204'
    assert state['visit_draft']['unit_id']=='H-201'
    assert not state.get('confirmation_request')
