"""V13 agent-native commercial routing, persistent-vs-temporary, and SMS guards.

All contacts and homes are synthetic. These tests cover complete domain-turn
and delivery-policy behavior, not solely LLM prompt wording.
"""
from app import commercial_turn, delivery_policy, domain


def established():
    s=domain.blank_state()
    domain.begin_turn(s,'I am looking for a 2-bedroom house')
    assert domain.verify_explicit_preferences(s,'I am looking for a 2-bedroom house')['ok']
    domain.end_turn(s,'Great, do you have a budget?')
    domain.begin_turn(s,"I don't want to spend more than $300k")
    assert domain.verify_explicit_preferences(s,"I don't want to spend more than $300k")['ok']
    domain.end_turn(s,'I can look for houses under $300k.')
    assert s['search_preferences']['bedrooms']['preferred']==2
    assert s['search_preferences']['budget']['maximum_usd']==300000
    return s


def test_temporary_search_does_not_replace_preferred_bedrooms_or_budget():
    s=established()
    domain.begin_turn(s,'First I want to check if there is another option with 3 bedrooms under my budget')
    assert s['_turn_intent']=='property_search'
    assert s['_turn_scope']=='temporary'
    assert domain.verify_explicit_preferences(s,s['_active_source_message_text'])['ok']
    out=domain.search_properties(s)
    assert [u['unit_id'] for u in out['matching_properties']]==['H-204']
    assert out['scope']=='temporary'
    assert not out['primary_preferences_modified']
    assert s['search_preferences']['bedrooms']['preferred']==2
    assert s['search_preferences']['budget']['maximum_usd']==300000
    assert s['active_search']['criteria']['bedrooms']==3
    assert s['shortlisted_unit_ids']==['H-204']


def test_temporary_search_during_booking_pauses_not_cancels_draft():
    s=established()
    domain.begin_turn(s,'I want to visit H-201')
    assert s['visit_draft']['unit_id']=='H-201'
    s['visit_draft'].update(date='2026-11-26',start_time='15:00',
                            proposal_id='example',status='awaiting_confirmation')
    s['confirmation_request']={'unit_id':'H-201','date':'2026-11-26',
                               'start_time':'15:00','proposal_id':'example'}
    domain.end_turn(s,'For H-201 on 26 Nov at 3 PM, reply Yes, I confirm.')
    domain.begin_turn(s,'Show me other houses with 3 bedrooms under my budget')
    assert s['visit_draft']['unit_id']=='H-201'
    assert s['visit_draft']['start_time']=='15:00'
    assert s['visit_planning_paused'] is True
    assert s['confirmation_request'] is None
    assert s['conversation_phase']=='exploration'
    out=domain.search_properties(s)
    assert [x['unit_id'] for x in out['matching_properties']]==['H-204']
    assert not domain.ready_to_finalize_visit(s)
    assert s['visit_draft']['unit_id']=='H-201'


def test_v12_old_property_price_repair_never_overrides_new_search():
    s=established()
    domain.begin_turn(s,'I mean I want any other property with 3 bedrooms under my budget')
    out=domain.search_properties(s)
    assert out['ok'] and [x['unit_id'] for x in out['matching_properties']]==['H-204']
    answer=delivery_policy.validate_outbound(
        'Two-Bedroom Garden House (H-201) is listed for USD 245,000 (synthetic).',s)
    assert 'H-204' in answer
    assert 'H-201' not in answer
    assert '3 beds' in answer


def test_missing_hermes_search_tool_gets_catalog_grounded_recovery():
    s=established()
    domain.begin_turn(s,'Are there other houses with 3 bedrooms under my budget?')
    response=delivery_policy.validate_outbound(
        'The H-201 house costs $245,000.',s)
    assert 'H-204' in response and 'H-201' not in response
    assert s.get('_automatic_search_recovery')
    assert s['search_preferences']['bedrooms']['preferred']==2


def test_search_results_persist_across_turns_but_not_as_permanent_preferences():
    s=established()
    domain.begin_turn(s,'Show me other options with 3 bedrooms')
    out=domain.search_properties(s)
    domain.end_turn(s,delivery_policy.validate_outbound(
        'H-204 has 3 bedrooms for $295k. Would you like to explore it?',s))
    assert s['active_search']['scope']=='temporary'
    assert s['conversation_phase']=='exploration'
    domain.begin_turn(s,'What is the area of H-204?')
    assert s['_turn_intent']=='property_details'
    assert domain.unit_details(s)['area_sqm']==135
    assert s['search_preferences']['bedrooms']['preferred']==2


def test_explicit_revision_changes_primary_bedrooms():
    s=established()
    domain.begin_turn(s,'Actually, now I prefer 3 bedrooms')
    assert s['_turn_scope']=='permanent'
    assert domain.verify_explicit_preferences(s,s['_active_source_message_text'])['ok']
    assert s['search_preferences']['bedrooms']['preferred']==3


def test_exploratory_rewrite_cannot_be_forced_permanent_by_model():
    s=established()
    domain.begin_turn(s,'Could you show me other homes with 3 bedrooms?')
    result=domain.search_properties(s,criteria={'bedrooms':3},scope='permanent')
    assert result['ok'] and result['scope']=='temporary'
    assert s['search_preferences']['bedrooms']['preferred']==2
    result2=domain.update_search_preferences(s,bedrooms_mode='preferred',bedrooms_preferred=3)
    assert result2.get('scope')=='temporary'
    assert s['search_preferences']['bedrooms']['preferred']==2


def test_model_cannot_inject_unsupported_query_values():
    s=established()
    domain.begin_turn(s,'Show me other houses with 3 bedrooms')
    result=domain.search_properties(s,criteria={'bedrooms':4})
    assert result['error_code']=='SEARCH_CRITERIA_INVALID'
    assert s['search_preferences']['bedrooms']['preferred']==2


def test_temporary_searches_do_not_accumulate_stale_bedroom_filters():
    s=established()
    domain.begin_turn(s,'Show me 3 bedroom options')
    assert domain.search_properties(s)['matching_properties'][0]['unit_id']=='H-204'
    domain.end_turn(s,'H-204 has 3 bedrooms.')
    domain.begin_turn(s,'Show me 2 bedroom options instead')
    out=domain.search_properties(s)
    assert [x['unit_id'] for x in out['matching_properties']]==['H-201']
    assert s['search_preferences']['bedrooms']['preferred']==2


def test_more_expensive_temporary_range_does_not_raise_primary_budget():
    s=established()
    domain.begin_turn(s,'Can you show me homes under $350k?')
    out=domain.search_properties(s)
    codes={x['unit_id'] for x in out['matching_properties']}
    assert 'H-101' not in codes  # 3 bedrooms differs from preferred 2
    assert 'H-201' in codes
    assert s['search_preferences']['budget']['maximum_usd']==300000
    assert s['active_search']['effective_preferences']['budget']['maximum_usd']==350000


def test_criteria_simultaneous_area_bathrooms_and_bedrooms():
    s=established()
    domain.begin_turn(s,'Show me houses with 3 bedrooms, at least 2 bathrooms, at least 120 m2')
    out=domain.search_properties(s)
    assert 'H-204' in {x['unit_id'] for x in out['matching_properties']}
    assert 'H-201' not in {x['unit_id'] for x in out['matching_properties']}
    assert s['active_search']['effective_filters']['min_area_sqm']==120
    assert s['active_search']['effective_filters']['min_bathrooms']==2


def test_no_catalog_pdfs_unprompted_but_suggest_fact_sheet_after_gallery():
    s=domain.blank_state()
    domain.begin_turn(s,'Please send photos of H-201')
    assert s['_turn_intent']=='property_assets'
    media=domain.unit_media('H-201')
    s['_approved_media_urls_this_turn']=[media['gallery_url']]
    answer=delivery_policy.validate_outbound('Photos: '+media['gallery_url'],s)
    assert media['gallery_url'] in answer
    assert 'PDF fact sheet' in answer
    assert not any(d['url'] in answer for d in media['documents'])
    domain.end_turn(s,answer)
    assert 'documents' in s['offered_assets']['H-201']


def test_repeated_fact_sheet_offer_is_not_spam():
    s=domain.blank_state()
    domain.begin_turn(s,'Please send photos of H-201')
    media=domain.unit_media('H-201')
    s['_approved_media_urls_this_turn']=[media['gallery_url']]
    answer=delivery_policy.validate_outbound('Photos: '+media['gallery_url'],s)
    domain.end_turn(s,answer)
    domain.begin_turn(s,'Can you send the photos again?')
    s['_approved_media_urls_this_turn']=[media['gallery_url']]
    s['_media_resend_urls_this_turn']=[media['gallery_url']]
    again=delivery_policy.validate_outbound('Photos: '+media['gallery_url'],s)
    assert 'resending' in again.lower()
    assert again.count('fact sheet')==0


def test_photo_and_fact_sheet_requests_do_not_trigger_search():
    for question in ('Show me the gallery for H-201','Do you have a brochure?',
                     'Show me more details of H-201'):
        assert commercial_turn.intent(question)!='property_search'


def test_search_sessions_are_traced_and_visit_remains_independent():
    s=established()
    domain.begin_turn(s,'Show me other houses with 3 bedrooms')
    result=domain.search_properties(s)
    domain.end_turn(s,delivery_policy.validate_outbound(
        'H-204 (3 beds, $295k). Want details?',s))
    assert s['search_sessions'][-1]['matching_unit_ids']==['H-204']
    assert s['search_preferences_history']
    assert s['conversation_transitions'][-1]['phase']=='exploration'
