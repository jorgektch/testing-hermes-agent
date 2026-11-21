"""Installation preflight against a minimal v13.3 fixture (offline)."""
from __future__ import annotations
import pathlib, sys, subprocess, ast, shutil
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import apply
MOCKS={'README.md': '# Hermes Agent Lab · Asesor inmobiliario conversacional (v13.3)\n'
              '\n'
              '### Selección de opciones (v13.3)\n'
              '\n'
              'Muestra una lista de propiedades y guarda las opciones.\n',
 'backend/app/commercial_turn.py': 'from . import commercial_continuity as continuity\n'
                                   '\n'
                                   'def search_request(message):\n'
                                   "    text=str(message or '')\n"
                                   '    if continuity.is_calendar_question(text):\n'
                                   '        return False\n'
                                   '    return True\n'
                                   '\n'
                                   'def intent(message: str) -> str:\n'
                                   "    text=str(message or '')\n"
                                   '    state=state or {}\n'
                                   "    draft=state.get('visit_draft') or {}\n"
                                   "    return 'property_search'\n",
 'backend/app/conversation_context.py': 'from typing import Any\n'
                                        '\n'
                                        'def resolved_reference(state, text, units):\n'
                                        "    recent=[c for c in (state.get('last_presented_units') "
                                        'or []) if c in units]\n'
                                        '    return recent\n',
 'backend/app/delivery_policy.py': 'from . import commercial_continuity as continuity\n'
                                   '\n'
                                   'def _matching_reply(matches, alternatives=None):\n'
                                   "    return 'old'\n"
                                   '\n'
                                   'def _no_matches_for_state(state):\n'
                                   "    return 'no'\n"
                                   '\n'
                                   'def validate_outbound(reply, state):\n'
                                   '    plain=reply\n'
                                   '    # The language model cannot promote a selected hour to a '
                                   'confirmed visit.\n'
                                   '    commercial_search=True\n'
                                   '    search_results=[]\n'
                                   '    _record_sent_media(state,plain)\n'
                                   '    return plain\n',
 'backend/app/domain.py': 'from . import scheduling, sales_preferences, conversation_context, '
                          'property_filters, commercial_turn, search_sessions, '
                          'commercial_continuity as continuity\n'
                          '\n'
                          'def begin_turn(state,message):\n'
                          "    state['_turn_intent']=commercial_turn.intent(message)\n"
                          '\n'
                          'def blank_state():\n'
                          '    return {\n'
                          "        'active_search': None,\n"
                          '    }\n'
                          '\n'
                          'def _observe_inbound_context(state, message):\n'
                          '    known={}\n'
                          '    resolution={}\n'
                          "    state['_turn_property_resolution']=resolution\n"
                          '    return state\n'
                          '\n'
                          'def end_turn(state, reply):\n'
                          "    bp={'sales_messages':[{'id':'id'}]}\n"
                          '    '
                          'new_mentions=conversation_context.presented_units(reply,_units_by_code())\n'
                          "    state.pop('_search_tool_called_this_turn', None)\n"
                          '\n'
                          'def evaluate_properties(state,criteria=None):\n'
                          "    lead = state.get('lead', {})\n"
                          '    return lead\n'
                          '\n'
                          'def search_properties(state,criteria=None,scope=None):\n'
                          "    utterance=str(state.get('_active_source_message_text') or '')\n"
                          "    return {'utterance':utterance}\n"
                          '\n'
                          'def slots(state, date_str=None):\n'
                          '    known = _units_by_code()\n'
                          '    return known\n'
                          '\n'
                          "def track_property_interest(state, unit_id, status='discussed'):\n"
                          "    if status == 'interested' and "
                          "state.get('_active_source_message_id'):\n"
                          "        return {'ok':True}\n"
                          '\n'
                          'def profile_snapshot(state):\n'
                          '    return {\n'
                          "        'active_search':copy.deepcopy(state.get('active_search')),\n"
                          '    }\n',
 'backend/app/hermes_runtime.py': "PROMPT = '''\n"
                                  '- Prefer answering with the most relevant 1-3 matches: property '
                                  'code, bedroom\n'
                                  '  count, price and one relevant distinction. Ask which to '
                                  'explore only after\n'
                                  "  answering the buyer's question. If nothing matches, say so "
                                  'clearly.\n'
                                  "'''\n"
                                  '\n'
                                  'def context(state):\n'
                                  '    verified={\n'
                                  '        '
                                  "'last_presented_units':state.get('last_presented_units',[]),\n"
                                  '    }\n'
                                  '    return verified\n',
 'backend/app/sms.py': 'import re\n'
                       '_MAX_LOGICAL_SMS_SEGMENTS=2\n'
                       '\n'
                       'def normalize(text):\n'
                       '    return text\n'
                       '\n'
                       'def details(text):\n'
                       "    return {'segments':2}\n"
                       '\n'
                       'def outgoing_segments(text):\n'
                       '    plain=normalize(text)\n'
                       "    if details(plain)['segments'] == 1:\n"
                       '        return [plain]\n'
                       '    return [plain]\n',
 'frontend/src/app/globals.css': '.message-content p{margin:0}\n'}


# Match the actual v13.3 commit, which does not import continuity here.
MOCKS['backend/app/commercial_turn.py']=(
    MOCKS['backend/app/commercial_turn.py']
    .replace('from . import commercial_continuity as continuity', 'import re')
    .replace('    if continuity.is_calendar_question(text):\n        return False\n', '')
    .replace('    state=state or {}\n', '')
    .replace("    draft=state.get('visit_draft') or {}\n", '    draft={}\n'))
MOCKS['backend/app/domain.py']=MOCKS['backend/app/domain.py'].replace(
    ', commercial_continuity as continuity\n', '\n')
MOCKS['backend/app/delivery_policy.py']=MOCKS['backend/app/delivery_policy.py'].replace(
    'from . import commercial_continuity as continuity', 'from . import sms')

def make_checkout(tmp_path):
    for path,content in MOCKS.items():
        target=tmp_path/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding='utf-8')
    for path,original in [
        ('backend/app/property_options.py','property_options_base.py'),
        ('backend/tests/test_v13_3_option_selection.py','test_v13_3_option_selection_base.py')]:
        target=tmp_path/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text((ROOT/original).read_text(encoding='utf-8'),encoding='utf-8')
    target=tmp_path/'backend/app/commercial_continuity.py'
    target.write_text('def is_calendar_question(*args,**kwargs):return False\n',encoding='utf-8')
    return tmp_path


def test_dry_run_never_writes(tmp_path):
    repo=make_checkout(tmp_path)
    result=subprocess.run([sys.executable,str(ROOT/'apply.py'),'--repo',str(repo),'--dry-run','--no-git-check'],
                         capture_output=True,text=True)
    assert result.returncode==0, result.stderr
    assert '10 files' in result.stdout
    assert (repo/'backend/app/commercial_turn.py').read_text()==MOCKS['backend/app/commercial_turn.py']
    assert not (repo/'v13_3_1_changes.patch').exists()


def test_installed_transform_links_all_modules(tmp_path):
    repo=make_checkout(tmp_path)
    old,new=apply.transform(repo)
    assert len(new)==10
    for name,text in new.items():
        if name.endswith('.py'):ast.parse(text,filename=name)
        (repo/name).write_text(text,encoding='utf-8')
    assert "intent(message,state=state)" in new['backend/app/domain.py']
    assert "state['conversation_phase']='property_details'" in new['backend/app/domain.py']
    assert "state.get('_turn_intent')=='property_selection'" in new['backend/app/domain.py']
    assert 'last_presented_property_options' in new['backend/app/domain.py']
    assert 'property_options.render_matches' in new['backend/app/delivery_policy.py']
    assert 'property_options.needs_listing_repair' in new['backend/app/delivery_policy.py']
    assert "if '\\n' in plain" in new['backend/app/sms.py']
    assert 'property_options.resolve_selection' in new['backend/app/conversation_context.py']
    assert 'property_options.is_option_selection' in new['backend/app/commercial_turn.py']
    assert 'selected_property_option' in new['backend/app/hermes_runtime.py']
    assert 'message-content li' in new['frontend/src/app/globals.css']
    with pytest.raises(apply.PatchError):apply.transform(repo)


def test_missing_anchor_aborts_entire_installation(tmp_path):
    repo=make_checkout(tmp_path)
    target=repo/'backend/app/commercial_turn.py'
    target.write_text(target.read_text().replace('import re\n',''),encoding='utf-8')
    with pytest.raises(apply.PatchError): apply.transform(repo)
    assert (repo/'backend/app/conversation_context.py').read_text()==MOCKS['backend/app/conversation_context.py']
    assert (repo/'backend/app/property_options.py').read_text()==(ROOT/'property_options_base.py').read_text()


def test_cli_applies_patch_locally(tmp_path):
    repo=make_checkout(tmp_path)
    p=subprocess.run([sys.executable,str(ROOT/'apply.py'),'--repo',str(repo),'--no-git-check'],
                     capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert (repo/'v13_3_1_changes.patch').exists()
    content=(repo/'v13_3_1_changes.patch').read_text()
    assert 'backend/app/property_options.py' in content
    assert 'backend/app/domain.py' in content
    result=subprocess.run([sys.executable,str(ROOT/'apply.py'),'--repo',str(repo),'--no-git-check','--dry-run'],
                         capture_output=True,text=True)
    assert result.returncode!=0
    assert 'PRECHECK FAILED' in result.stderr
