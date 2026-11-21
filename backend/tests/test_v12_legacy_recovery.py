"""Anonymized regression of a persisted pre-v12 'confirm again' loop."""
from app import delivery_policy, domain
from main import _begin_turn, _hydrate_legacy_context


def test_exported_old_conversation_can_resume_public_square_footage():
    state=domain.blank_state()
    state['shortlisted_unit_ids']=['H-201']
    state['conversation_focus']['unit_id']=None
    state['exploring_unit_id']=None
    state['last_presented_units']=[]
    state['last_assistant_reply']=None
    history=[
        {'role':'assistant','content':'I found H-201 for $245,000. Want more details?'},
        {'role':'user','content':'What is the square footage?'},
        {'role':'assistant','content':'Please confirm again that you are interested in H-201.'},
        {'role':'user','content':'Yes'},
        {'role':'assistant','content':'Thank you for your patience!'},
    ]
    _hydrate_legacy_context(state,history)
    assert state['last_presented_units']==['H-201']
    assert state['_pending_fact_hint']=='area'
    _begin_turn(state,'Yes')
    assert state['conversation_focus']['unit_id']=='H-201'
    assert state['_pending_factual_topic_override']=='area'
    assert state['_details_for_current_turn']['area_sqm']==110
    reply=delivery_policy.validate_outbound(
        'I still need to confirm that you want to explore H-201.',state)
    assert '1,184 sq ft' in reply and '1,561 sq ft' in reply
    assert 'confirm' not in reply.lower()
    domain.end_turn(state,reply)
    assert state['answered_topics'][-1]['topic']=='area'
    assert state['visit_draft']['unit_id'] is None


def test_legacy_hydration_never_overrides_a_pending_booking_confirmation():
    state=domain.blank_state()
    state['confirmation_request']={'unit_id':'H-201','date':'2026-11-25',
                                   'start_time':'15:00','proposal_id':'test',
                                   'prompt_message_id':'some-id'}
    messages=[
        {'role':'user','content':'What is the area?'},
        {'role':'assistant','content':'Please confirm again that you are interested in H-201.'},
    ]
    _hydrate_legacy_context(state,messages)
    assert not state.get('_pending_fact_hint')
    _begin_turn(state,'Yes')
    assert not state.get('_pending_factual_topic_override')
    assert state['visit_draft']['unit_id'] is None
