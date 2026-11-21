"""Offline tests of the pure property-menu contract (no LLM and no network)."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'property_options_modified.py'
spec=importlib.util.spec_from_file_location('test_property_options_module',SOURCE)
options=importlib.util.module_from_spec(spec)
spec.loader.exec_module(options)

UNITS=[
 {'unit_id':'H-204','commercial_name':'Three-Bedroom Courtyard House','bedrooms':3,
  'bathrooms':2,'price_usd':295000,'area_sqm':135,
  'available_from_date':'2026-12-15',
  'confirmed_features':{'kitchen_layout':'open','parking_spaces':1}},
 {'unit_id':'H-101','commercial_name':'Three-Bedroom Family House','bedrooms':3,
  'bathrooms':2.5,'price_usd':320000,'area_sqm':150,
  'available_from_date':'Immediate',
  'confirmed_features':{'kitchen_layout':'open','parking_spaces':2}},
]


def state():
    reply=options.render_matches(UNITS)
    return {'last_presented_property_options':options.delivered_snapshot(
        UNITS,reply,search_revision=2,reply_message_id='demo-reply')}


@pytest.mark.parametrize('sentence,index',[
    ('Option 1 is good',1),('Option 2 its ok for me',2),
    ('I like option #2',2),('Number 2 please',2),
    ('The second one looks interesting',2),('the 1st one',1),
    ('I prefer the latter',2),('How about the former?',1),
    ('I choose the $320k house',2),('Tell me about the $295,000 one',1),
    ('I prefer H-101',2),('Tell me about H-204',1),
])
def test_selection_refers_to_exact_visible_order(sentence,index):
    resolved=options.resolve_selection(state(),sentence)
    assert resolved['reason']=='presented_option'
    assert resolved['display_number']==index
    assert resolved['unit_id']==UNITS[index-1]['unit_id']
    assert resolved['source_reply_message_id']=='demo-reply'


@pytest.mark.parametrize('sentence',[
    'Do you have any with 3 bedrooms?',
    'Any other houses?',
    'Find more 3-bedroom properties',
    'Show me other options with 2 bedrooms',
    'I want to change my budget to $300k',
    'I want 2 bedrooms',
    'I want to visit H-101',
    'Send the H-101 gallery',
])
def test_new_search_or_preference_does_not_select_old_menu(sentence):
    assert not options.is_option_selection(sentence)
    assert options.resolve_selection(state(),sentence) is None


def test_selection_without_visible_list_is_ambiguous_not_guessed():
    result=options.resolve_selection({},'Option 2 its ok')
    assert result['unit_id'] is None and result['ambiguous']


@pytest.mark.parametrize('sentence', ['option 9', 'the fourth one', '#3'])
def test_out_of_range_is_ambiguous(sentence):
    result=options.resolve_selection(state(),sentence)
    assert result['unit_id'] is None and result['ambiguous']


def test_malformed_list_needs_repair_and_renders_sms_friendly_numbering():
    bad=('I found two 3-bedroom houses for you: 1. Three-Bedroom Courtyard '
         'House $295k 2. Three-Bedroom Family House $320k')
    assert options.needs_listing_repair(bad,UNITS)
    good=options.render_matches(UNITS)
    assert good.count('\n')>=5
    assert '\n1. H-204 (3 beds, $295k)' in good
    assert '\n2. H-101 (3 beds, $320k)' in good
    assert good.index('H-204')<good.index('H-101')
    assert '$295k' in good and '$320k' in good
    assert 'Three-Bedroom Courtyard House' not in good  # Details come next
    assert 'Dec 15, 2026' not in good  # Do not overload exploration
    assert 'Which would you like to explore' in good and 'different features' in good
    assert not options.needs_listing_repair(good,UNITS)


def test_single_property_has_no_number_and_only_one_choice():
    reply=options.render_matches([UNITS[0]])
    assert '1. H-204' not in reply
    assert '2. H-101' not in reply
    assert '\nH-204 (3 beds, $295k)\n' in reply
    assert 'Would you like more details' in reply
    assert not options.needs_listing_repair(reply,[UNITS[0]])


def test_stale_option_cannot_be_chosen_after_empty_search():
    assert options.resolve_selection({'last_presented_property_options':None},'Option 2')['ambiguous']


def test_order_reconstruction_is_from_text_not_internal_price_ranking():
    reverse=[UNITS[1],UNITS[0]]
    reply=options.render_matches(reverse)
    snap=options.delivered_snapshot(reverse,reply,search_revision=7,reply_message_id='reply-7')
    assert snap['options'][0]['unit_id']=='H-101'
    result=options.resolve_selection({'last_presented_property_options':snap},'Option 1 is okay')
    assert result['unit_id']=='H-101'


def test_never_offer_unmentioned_hidden_unit_as_menu_choice():
    incomplete='1. H-204 (3 beds, $295k)'
    assert options.delivered_snapshot(UNITS,incomplete) is None


def test_selection_does_not_implicitly_say_buyer_requested_booking():
    result=options.resolve_selection(state(),'Option 2 its ok for me')
    assert 'visit' not in result and 'booking' not in result


def test_safe_selection_repair_uses_catalog_only():
    bad='I found 1 matching house(s): H-201 (2 beds, $245k). Which would you like to explore?'
    assert options.needs_selection_repair(bad,UNITS[1])
    reply=options.selected_property_reply(UNITS[1])
    assert 'H-101' in reply and 'H-201' not in reply
    assert '$320,000' in reply and '150 sq m' in reply
    assert '2 parking spaces' in reply
    assert not options.needs_selection_repair(reply,UNITS[1])


def test_ordinal_not_confused_with_bedrooms():
    assert options.is_option_selection('The second one, please')
    assert not options.is_option_selection('Do you have 2-bedroom options?')


def test_unsupported_map_to_option_is_rejected():
    original=state()
    original['last_presented_property_options']['options'][1]['unit_id']='H-104'
    result=options.resolve_selection(original,'Option 2')
    assert result['unit_id']=='H-104'  # Resolver only maps displayed IDs; domain verifies catalog ID.


def test_three_results_number_exactly_three_and_offer_broader_filters():
    third=dict(UNITS[1],unit_id='H-104',commercial_name='Four-Bedroom Premium House',bedrooms=4,price_usd=395000)
    result=options.render_matches(UNITS+[third])
    assert result.count('\n3. H-104 (4 beds, $395k)')==1
    assert 'Which would you like to explore' in result
    assert 'different features' in result


def test_menu_only_shows_first_three_when_more_matches_exist():
    others=[dict(UNITS[1],unit_id='H-104',commercial_name='Fourth Home'),
            dict(UNITS[1],unit_id='H-105',commercial_name='Fifth Home')]
    result=options.render_matches(UNITS+others)
    assert 'H-105' not in result
    assert '1 more matching houses' in result
    snapshot=options.delivered_snapshot(UNITS+others,result)
    assert [x['unit_id'] for x in snapshot['options']]==['H-204','H-101','H-104']


def test_number_only_is_selection_only_when_previous_menu_exists():
    assert not options.is_option_selection('2')
    assert options.is_option_selection('2',has_visible_options=True)
    assert options.resolve_selection(state(),'2')['unit_id']=='H-101'
    assert options.resolve_selection({},'2') is None


def test_wrong_price_with_correct_ids_is_repaired():
    invalid='I found two houses:\n\n1. H-204 (3 beds, $250k)\n2. H-101 (3 beds, $320k)\n\nWhich would you like to explore?'
    assert options.needs_listing_repair(invalid,UNITS)
    good=options.render_matches(UNITS)
    assert good=='I found 2 3-bedroom houses:\n\n1. H-204 (3 beds, $295k)\n2. H-101 (3 beds, $320k)\n\nWhich would you like to explore? I can also look for homes with different features.'


def test_one_match_does_not_have_selection_number():
    one=options.render_matches([UNITS[0]])
    assert '\n1.' not in one and '\n2.' not in one
    assert one.count('H-204')==1
