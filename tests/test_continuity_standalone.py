import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'app'))
import commercial_continuity as c
import pytest

@pytest.mark.parametrize('q', [
    'What about the other days that week?',
    'Which day next week can I visit at 8:00 a.m.?',
    'Are there any other dates or times?',
    '9am its ok',
    'What if we visit on November 24?',
])
def test_calendar_intent(q):
    assert c.is_calendar_question(q, has_visit=True, phase='visit_planning')

@pytest.mark.parametrize('q', ['Show me other houses with three bedrooms', 'Do you have other properties?', 'I want to see a cheaper house'])
def test_not_calendar_intent(q):
    assert not c.is_calendar_question(q,has_visit=True,phase='visit_planning')

@pytest.mark.parametrize('text,expected', [('8:00am','08:00'),('9am its ok','09:00'),('12 PM','12:00'),('12:00 am','00:00'),('14:00','14:00'),('8am or 9am',None),('no time',None)])
def test_clock(text,expected):
    assert c.clock_value(text)==expected

def test_anchored_house_references():
    assert c.explicit_visit_switch('Actually I want to visit H-201 instead','H-201','H-204')
    assert not c.explicit_visit_switch('How much is H-201?','H-201','H-204')
    assert not c.explicit_visit_switch('We can visit it at 9am','H-201','H-204')

@pytest.mark.parametrize('q,want', [('November 24','2026-11-24'),('24th works','2026-11-24'),('9am is fine',None),('$24k more',None),('November 24 and 25',None),('2026-11-23','2026-11-23')])
def test_date(q,want):
    offered=['2026-11-23','2026-11-24','2026-11-25']
    assert c.explicitly_selected_date(q,offered)==want


def test_preference_offer_lifecycle():
    state={'search_preferences':{'revision':2,'bedrooms':{'preferred':2},'budget':{'maximum_usd':200000}},'lead':{}}
    unit={'unit_id':'H-204','bedrooms':3,'price_usd':295000}
    assert c.maybe_propose_preference_update(state,unit,source_message_id='message1')
    assert c.maybe_propose_preference_update(state,unit,source_message_id='message1') is None
    assert c.preference_offer_decision(state,'Yes update both preferences for H204',{'H-204':unit})['action']=='none'
    text=c.preference_offer_text(state,unit)
    assert '$95,000' in text and '3 bedrooms' in text
    state['preference_update_proposals']['H-204']['status']='offered'
    assert c.preference_offer_decision(state,'9am its ok',{'H-204':unit})['action']=='none'
    accepted=c.preference_offer_decision(state,'Yes, update both preferences for H204',{'H-204':unit})
    assert accepted['action']=='update' and set(accepted['fields'])=={'budget','bedrooms'}
    assert state['search_preferences']['budget']['maximum_usd']==200000
    decline=c.preference_offer_decision(state,'Keep my original budget and treat H204 as an exception',{'H-204':unit})
    assert decline['action']=='exception_only' and state['preference_update_proposals']['H-204']['status']=='exception_only'

def test_other_verified_filters_can_be_proposed_without_mutation():
    state={'search_preferences':{'revision':3,'bedrooms':{'preferred':3},'budget':{'maximum_usd':300000}},
           'property_filters':{'min_area_sqm':150.0,'min_parking_spaces':2,'kitchen_layout':'closed'},'lead':{}}
    unit={'unit_id':'H-204','bedrooms':3,'price_usd':295000,'area_sqm':135,'bathrooms':2,
          'confirmed_features':{'parking_spaces':1,'kitchen_layout':'open','private_backyard':True}}
    offer=c.maybe_propose_preference_update(state,unit,source_message_id='user-msg')
    assert set(offer['suggested_filter_changes'])=={'min_area_sqm','min_parking_spaces','kitchen_layout'}
    assert 'different' in c.preference_offer_text(state,unit)
    offer['status']='offered'
    decision=c.preference_offer_decision(state,'Yes update all those search preferences for H204',{'H-204':unit})
    assert decision['action']=='update' and decision['fields']==['filters']
    assert state['property_filters']['min_area_sqm']==150.0
