"""Offline installer contract: all edits preflight before writes, syntax valid."""
import importlib.util
import pathlib
import subprocess
import sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('hermes_options_installer',ROOT/'apply.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


MOCKS={
'backend/app/commercial_turn.py':'''from . import commercial_continuity as continuity

def search_request(message):
    text=str(message or '')
    if continuity.is_calendar_question(text):
        return False
    return True

def intent(message: str, state: dict | None = None) -> str:
    text=str(message or '')
    state=state or {}
    draft=state.get('visit_draft') or {}
    return 'property_search'
''',
'backend/app/conversation_context.py':'''from typing import Any

def resolved_reference(state, text, units):
    recent=[c for c in (state.get('last_presented_units') or []) if c in units]
    return recent
''',
'backend/app/domain.py':'''from . import scheduling, sales_preferences, conversation_context, property_filters, commercial_turn, search_sessions, commercial_continuity as continuity

def blank_state():
    return {
        'active_search': None,
    }

def _observe_inbound_context(state, message):
    known={}
    resolution={}
    state['_turn_property_resolution']=resolution
    return state

def end_turn(state, reply):
    bp={'sales_messages':[{'id':'id'}]}
    new_mentions=conversation_context.presented_units(reply,_units_by_code())
    state.pop('_search_tool_called_this_turn', None)

def evaluate_properties(state,criteria=None):
    lead = state.get('lead', {})
    return lead

def search_properties(state,criteria=None,scope=None):
    utterance=str(state.get('_active_source_message_text') or '')
    return {'utterance':utterance}

def slots(state, date_str=None):
    known = _units_by_code()
    return known

def track_property_interest(state, unit_id, status='discussed'):
    if status == 'interested' and state.get('_active_source_message_id'):
        return {'ok':True}

def profile_snapshot(state):
    return {
        'active_search':copy.deepcopy(state.get('active_search')),
    }
''',
'backend/app/delivery_policy.py':'''from . import commercial_continuity as continuity

def _matching_reply(matches, alternatives=None):
    return 'old'

def _no_matches_for_state(state):
    return 'no'

def validate_outbound(reply, state):
    plain=reply
    # The language model cannot promote a selected hour to a confirmed visit.
    commercial_search=True
    search_results=[]
    _record_sent_media(state,plain)
    return plain
''',
'backend/app/hermes_runtime.py':'''PROMPT = \'\'\'
- Prefer answering with the most relevant 1-3 matches: property code, bedroom
  count, price and one relevant distinction. Ask which to explore only after
  answering the buyer's question. If nothing matches, say so clearly.
\'\'\'

def context(state):
    verified={
        'last_presented_units':state.get('last_presented_units',[]),
    }
    return verified
''',
'backend/app/sms.py':'''import re
_MAX_LOGICAL_SMS_SEGMENTS=2

def normalize(text):
    return text

def details(text):
    return {'segments':2}

def outgoing_segments(text):
    plain=normalize(text)
    if details(plain)['segments'] == 1:
        return [plain]
    return [plain]
''',
'frontend/src/app/globals.css':'.message-content p{margin:0}\n',
'README.md':'''# Hermes Agent Lab · Asesor inmobiliario conversacional (v13.2)

## Grafo comercial — no es un embudo rígido

**Memoria:** `active_search`/`search_sessions` = búsquedas temporales y definitivas;
''',
}


def fake_checkout(tmp_path):
    for rel,contents in MOCKS.items():
        dst=tmp_path/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text(contents,encoding='utf-8')
    (tmp_path/'backend/app/commercial_continuity.py').write_text('pass\n')
    return tmp_path


def test_editor_and_syntax_preflight(tmp_path):
    root=fake_checkout(tmp_path)
    old,new=installer.transform(root)
    assert len(new)==10
    for rel in MOCKS:
        assert old[rel]==(root/rel).read_text()
        assert new[rel]!=old[rel]
    assert 'property_selection' in new['backend/app/commercial_turn.py']
    assert 'selected_property_reply_recovery' in new['backend/app/delivery_policy.py']
    assert 'last_presented_property_options' in new['backend/app/domain.py']
    assert 'v13.3' in new['README.md']


def test_cli_dry_run_and_apply_then_reject_double_apply(tmp_path):
    root=fake_checkout(tmp_path)
    before=(root/'backend/app/domain.py').read_text()
    cmd=[sys.executable,str(ROOT/'apply.py'),'--repo',str(root)]
    dry=subprocess.run(cmd+['--dry-run'],text=True,capture_output=True)
    assert dry.returncode==0,dry.stdout+' '+dry.stderr
    assert (root/'backend/app/domain.py').read_text()==before
    assert not (root/'v13_3_changes.patch').exists()
    applied=subprocess.run(cmd,text=True,capture_output=True)
    assert applied.returncode==0,applied.stdout+' '+applied.stderr
    patch=root/'v13_3_changes.patch'
    assert patch.exists()
    assert '+++ b/backend/app/property_options.py' in patch.read_text()
    second=subprocess.run(cmd+['--dry-run'],text=True,capture_output=True)
    assert second.returncode!=0
    assert 'PRECHECK FAILED' in second.stderr


def test_declines_missing_v13_2_marker_without_writing(tmp_path):
    root=fake_checkout(tmp_path)
    (root/'backend/app/commercial_continuity.py').unlink()
    result=subprocess.run([sys.executable,str(ROOT/'apply.py'),'--repo',str(root)],
                          text=True,capture_output=True)
    assert result.returncode!=0
    assert not (root/'v13_3_changes.patch').exists()
