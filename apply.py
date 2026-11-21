#!/usr/bin/env python3
"""Apply Hermes v13.3 option-selection and readable-list differential LOCALLY.

Targets a clean checkout with the earlier Hermes v13.2 differential applied.
No network, GitHub, or deployment access. Writes a reviewable unified patch.
    python apply.py --repo /path/to/testing-hermes-agent --dry-run
    python apply.py --repo /path/to/testing-hermes-agent
"""
from __future__ import annotations

import argparse
import ast
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class PatchError(RuntimeError):
    pass

class Edit:
    def __init__(self, path: str, source: str):
        self.path = path
        self.text = source

    def replace(self, before: str, after: str, *, n: int = 1):
        hits = self.text.count(before)
        if hits != n:
            raise PatchError(f'{self.path}: expected {n} matching anchors, got {hits}: {before[:130]!r}')
        self.text = self.text.replace(before, after)

    def insert_before(self, anchor: str, text: str):
        self.replace(anchor, text + anchor)

    def insert_after(self, anchor: str, text: str):
        self.replace(anchor, anchor + text)

    def function_body_insert(self, name: str, anchor: str, text: str):
        start = self.text.find(f'def {name}(')
        if start < 0:
            raise PatchError(f'{self.path}: missing function {name}')
        end = self.text.find('\ndef ', start + 4)
        if end < 0:
            end = len(self.text)
        fragment = self.text[start:end]
        if fragment.count(anchor)!=1:
            raise PatchError(f'{self.path}: {name} expected one {anchor[:60]!r}, got {fragment.count(anchor)}')
        self.text=self.text[:start]+fragment.replace(anchor,anchor+text)+self.text[end:]


def commercial(source: str) -> str:
    e=Edit('backend/app/commercial_turn.py',source)
    e.insert_after('from . import commercial_continuity as continuity\n', 'from . import property_options\n')
    e.replace('    if continuity.is_calendar_question(text):\n        return False\n',
              '    if property_options.is_option_selection(text):\n        return False\n    if continuity.is_calendar_question(text):\n        return False\n')
    e.replace('    state=state or {}\n    draft=state.get(\'visit_draft\') or {}',
              "    state=state or {}\n    if property_options.is_option_selection(text):\n        return 'property_selection'\n    draft=state.get('visit_draft') or {}")
    return e.text


def context(source: str) -> str:
    e=Edit('backend/app/conversation_context.py',source)
    e.insert_after('from typing import Any\n','\nfrom . import property_options\n')
    # Explicit property code keeps its existing highest priority. Before the
    # legacy ordinal resolver, inspect the buyer-visible ordered snapshot.
    e.replace("    recent=[c for c in (state.get('last_presented_units') or []) if c in units]",
              """    if property_options.is_option_selection(text):
        selected=property_options.resolve_selection(state,text)
        if selected is not None:
            return selected
    recent=[c for c in (state.get('last_presented_units') or []) if c in units]""")
    return e.text


def domain(source: str) -> str:
    e=Edit('backend/app/domain.py',source)
    e.insert_after('from . import scheduling, sales_preferences, conversation_context, property_filters, commercial_turn, search_sessions, commercial_continuity as continuity\n',
                   'from . import property_options\n')
    e.replace("        'active_search': None,\n", "        'active_search': None,\n        'last_presented_property_options': None,\n")
    e.insert_after("    state['_turn_property_resolution']=resolution\n", """    if state.get('_turn_intent')=='property_selection':
        # The buyer chose a previously displayed option, not a new search or
        # an appointment. This transition is independent of visit_draft.
        selected=resolution.get('unit_id')
        if selected in known and not resolution.get('ambiguous'):
            draft=_draft(state)
            if draft.get('unit_id') and draft.get('status') not in ('idle','confirmed'):
                # Leave the old draft intact, but never allow a stale recap's
                # confirmation prompt to carry into a new property topic.
                state['visit_planning_paused']=True
                state['confirmation_request']=None
                state['pending_visit_offer']=None
            _set_focus(state,selected)
            state['conversation_phase']='property_details'
            state['stage']='Exploring property details'
            track_property_interest(state,selected,'considering')
            # Suggest (once) updating mismatched permanent filters, without
            # changing them or blocking details/visits.
            continuity.maybe_propose_preference_update(
                state,known[selected],source_message_id=state.get('_active_source_message_id'))
            state['_selected_property_option_this_turn']={
                'unit_id':selected,
                'display_number':resolution.get('display_number'),
                'source_reply_message_id':resolution.get('source_reply_message_id')}
        else:
            state['_turn_property_ambiguous']=True
        return
""")
    e.replace("    new_mentions=conversation_context.presented_units(reply,_units_by_code())\n", """    if (state.get('_turn_intent') in ('property_search','preference_update') and
            state.get('_matching_options_this_turn') is not None):
        options=property_options.delivered_snapshot(
            state['_matching_options_this_turn'],reply,
            search_revision=(state.get('active_search') or {}).get('revision'),
            reply_message_id=bp['sales_messages'][-1]['id'])
        # No match or omitted item means an old menu must not remain selectable.
        state['last_presented_property_options']=options
        if options:
            state['last_presented_units']=[o['unit_id'] for o in options['options']]
    new_mentions=conversation_context.presented_units(reply,_units_by_code())
""")
    e.replace("    state.pop('_search_tool_called_this_turn', None)\n",
              "    state.pop('_search_tool_called_this_turn', None)\n    state.pop('_selected_property_option_this_turn', None)\n")
    # Legacy catalog evaluation must not reinterpret a numbered choice as a
    # fresh buyer search (the primary-profile search would reinsert H-201).
    e.function_body_insert('evaluate_properties',
         "    lead = state.get('lead', {})\n",
         """    if state.get('_turn_intent')=='property_selection':
        return {'error':'The buyer selected an existing option, not a search.',
                'error_code':'PROPERTY_SELECTION_NOT_SEARCH',
                'next_action':'Use get_unit_details for the resolved property.'}
""")
    e.replace("    utterance=str(state.get('_active_source_message_text') or '')\n",
              """    if state.get('_turn_intent')=='property_selection':
        return {'error':'The buyer selected a displayed property, not a new search.',
                'error_code':'PROPERTY_SELECTION_NOT_SEARCH',
                'next_action':'Use get_unit_details for the resolved unit.'}
    utterance=str(state.get('_active_source_message_text') or '')
""")
    e.function_body_insert('slots', '    known = _units_by_code()\n', """    if state.get('_turn_intent')=='property_selection':
        return _slot_error('VISIT_NOT_REQUESTED',
            'The buyer asked about a property; no visit was requested.',
            next_action='Give the selected property details. Only check slots if the buyer asks to visit.')
""")
    e.replace("    if status == 'interested' and state.get('_active_source_message_id'):\n",
              """    if status == 'interested' and state.get('_turn_intent')=='property_selection':
        # An ordinal choice authorizes exploring details, not a visit or a
        # stronger declaration of interest from the agent.
        return {'error':'A displayed option was selected for details, not a visit.',
                'error_code':'INTEREST_LEVEL_NOT_DECLARED',
                'next_action':'The domain has already recorded considering status.'}
    if status == 'interested' and state.get('_active_source_message_id'):
""")
    e.insert_after("        'active_search':copy.deepcopy(state.get('active_search')),\n",
                   "        'last_presented_property_options':copy.deepcopy(state.get('last_presented_property_options')),\n")
    return e.text


def policy(source: str) -> str:
    e=Edit('backend/app/delivery_policy.py',source)
    e.insert_after('from . import commercial_continuity as continuity\n', 'from . import property_options\n')
    # Replace only the fallback's former formatting policy. Other guardrails
    # and the no-matches/alternative behavior stay unchanged.
    start=e.text.find('def _matching_reply(')
    end=e.text.find('\ndef _no_matches_for_state(',start)
    if start<0 or end<0:
        raise PatchError('backend/app/delivery_policy.py: matching reply function boundary not found')
    e.text=e.text[:start]+'''def _matching_reply(matches: list[dict], alternatives: list[dict] | None = None) -> str:
    """Verified menu fallback with visible ordinal-to-unit mapping."""
    alternatives=alternatives or []
    if matches:
        return property_options.render_matches(matches)
    if alternatives:
        alt=alternatives[0]
        return (f'No exact match. Optional alternative: {alt["unit_id"]} '
                f'({alt["bedrooms"]} bedrooms, ${int(alt["price_usd"]):,}), '
                'which differs from your preferences. Would you like its details?')
    return 'No houses meet every preference right now. Would you like to adjust a criterion?'

''' + e.text[end:]
    # Check buyer selection before any search-recovery fallback; no URL or
    # booking guard is relaxed. The backend can recover an erroneous LLM call
    # using only public published facts of the selected unit.
    e.insert_before('    # The language model cannot promote a selected hour to a confirmed visit.\n', '''    if state.get('_turn_intent')=='property_selection':
        from . import domain
        resolved=state.get('_turn_property_resolution') or {}
        unit=domain._units_by_code().get(resolved.get('unit_id'))
        if unit and not resolved.get('ambiguous'):
            if property_options.needs_selection_repair(plain,unit):
                plain=property_options.selected_property_reply(unit)
                state.setdefault('tool_observations',[]).append({
                    'tool':'selected_property_reply_recovery',
                    'status':'recovered','fields':['selected_unit_id'],
                    'message_id':state.get('_active_source_message_id'),
                    'timestamp':domain.now_local().isoformat()})
            _record_sent_media(state,plain)
            return _offer_preferences_after_interest(
                state,_suggest_available_assets(state,plain))
        return ('Which house would you like to explore? '
                'Please use its code, for example H-101.')
''')
    # The model can use different language, but the actual search listing
    # must contain every shown code, correct order and clear line breaks.
    # The earlier selection branch also records media, so patch the final
    # delivery path only (not the early property-selection return).
    anchor='    _record_sent_media(state,plain)\n'
    position=e.text.rfind(anchor)
    if position < 0:
        raise PatchError('backend/app/delivery_policy.py: final media-recording anchor missing')
    e.text=e.text[:position]+'''    if commercial_search and search_results:
        if property_options.needs_listing_repair(plain,search_results):
            plain=property_options.render_matches(search_results)
'''+e.text[position:]
    return e.text


def hermes(source: str) -> str:
    e=Edit('backend/app/hermes_runtime.py',source)
    e.insert_after('- Prefer answering with the most relevant 1-3 matches: property code, bedroom\n  count, price and one relevant distinction. Ask which to explore only after\n  answering the buyer\'s question. If nothing matches, say so clearly.\n',
     '''- Present one match WITHOUT an ordinal number. For two or more matches,
  display an ordered list: "1. H-204 - Name" and "2. H-101 - Name", one item
  per paragraph with real newlines, price and availability. End by offering
  details of an option OR further exploration with new criteria.
- When the buyer replies "Option 2", "the second one", or "the $320k house",
  that is a SELECTION of an already shown option, NOT a new property search.
  Answer the details of the selected property; do not call search_properties,
  visit slots or booking. Selection for details is not appointment consent.
''')
    e.insert_after("        'last_presented_units':state.get('last_presented_units',[]),\n",
                   "        'last_presented_property_options':state.get('last_presented_property_options'),\n        'selected_property_option':state.get('_selected_property_option_this_turn'),\n")
    return e.text


def sms(source: str) -> str:
    e=Edit('backend/app/sms.py',source)
    anchor="    if details(plain)['segments'] == 1:\n        return [plain]\n"
    e.insert_after(anchor, """    if '\\n' in plain:
        # Preserve the readable property menu for delivery by SMS, too.
        # Splitting on its blank lines is safe; recombining with spaces would
        # turn '1. ... 2. ...' into an ambiguous run-on paragraph.
        paragraphs=[p.strip() for p in re.split(r'\\n\\s*\\n',plain) if p.strip()]
        result=[]
        current=''
        for paragraph in paragraphs:
            candidate=(current+'\\n\\n'+paragraph) if current else paragraph
            if current and details(candidate)['segments']>_MAX_LOGICAL_SMS_SEGMENTS:
                result.append(current)
                current=paragraph
            else:
                current=candidate
        if current:
            result.append(current)
        return result
""")
    return e.text


def readme(source: str) -> str:
    e=Edit('README.md',source)
    if 'last_presented_property_options' in source and 'v13.3' in source:
        raise PatchError('README.md already describes v13.3; refusing duplicate installation')
    e.replace(' (v13.2)', ' (v13.3)',n=1)
    e.insert_before('## Grafo comercial — no es un embudo rígido\n', '''### Selección de opciones (v13.3)

Una búsqueda muestra **una** casa sin numeración; con varias, presenta una
lista corta con `1. H-204`, `2. H-101`, precios, disponibilidad y saltos de
línea legibles en el chat/SMS. El comprador puede responder «Option 2»,
«the second one» o referirse al precio. El backend vincula el número con el
**orden efectivamente enviado**, no con la posición del inmueble en catálogo.
Esta selección lleva a **Details** y registra interés de nivel `considering`;
no modifica presupuesto/dormitorios principales, no inicia una visita y no
autoriza reservas. La fase regresa a exploración únicamente si el comprador
solicita nuevos filtros u otras casas.

`last_presented_property_options` guarda el mensaje origen, la revisión de
búsqueda y los pares `(display_number, unit_id)` de opciones realmente mostradas.
El estado se reemplaza solo al entregar otra lista; una búsqueda sin resultados
lo invalida. Hermes/LLM explican y asesoran; el backend valida identidad,
numeración y hechos autorizados, con reparación del texto únicamente si la
respuesta omite códigos o confunde opciones. El render Markdown de Next.js
respeta los saltos de línea y la proyección SMS conserva párrafos separados.

''')
    e.replace('`active_search`/`search_sessions` = búsquedas temporales y definitivas;',
              '`active_search`/`search_sessions` = búsquedas temporales y definitivas; `last_presented_property_options` = última lista numerada y su mensaje origen;')
    return e.text

def styles(source: str) -> str:
    marker='/* v13.3 property option readability */'
    if marker in source:
        raise PatchError('frontend/src/app/globals.css already contains v13.3 styles')
    return source.rstrip('\n') + '\n' + marker + '\n.message-content p,.message-content li{white-space:pre-line}\n'


TRANSFORMS={
    'backend/app/commercial_turn.py':commercial,
    'backend/app/conversation_context.py':context,
    'backend/app/domain.py':domain,
    'backend/app/delivery_policy.py':policy,
    'backend/app/hermes_runtime.py':hermes,
    'backend/app/sms.py':sms,
    'frontend/src/app/globals.css':styles,
    'README.md':readme,
}
COPY=[
    'backend/app/property_options.py',
    'backend/tests/test_v13_3_option_selection.py',
]


def transform(repo: Path):
    if not (repo/'backend/app/commercial_continuity.py').is_file():
        raise PatchError('Requires a checkout with Hermes v13.2 already applied. Apply the previous v13.2 differential first.')
    old,new={},{}
    for rel,fn in TRANSFORMS.items():
        src=repo/rel
        if not src.is_file():
            raise PatchError('Missing source: '+rel)
        before=src.read_text(encoding='utf-8')
        after=fn(before)
        if rel.endswith('.py'):
            try:ast.parse(after,filename=rel)
            except SyntaxError as exc:raise PatchError(f'Invalid generated Python syntax in {rel}: {exc}') from exc
        old[rel],new[rel]=before,after
    for rel in COPY:
        dest=repo/rel
        if dest.exists():
            raise PatchError(f'Already exists: {rel}. Refusing to overwrite existing work.')
        before=''
        after=(ROOT/rel).read_text(encoding='utf-8')
        ast.parse(after,filename=rel)
        old[rel],new[rel]=before,after
    return old,new


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--print-diff',action='store_true')
    args=parser.parse_args()
    repo=args.repo.expanduser().resolve()
    try:
        old,new=transform(repo)
    except (OSError,PatchError) as exc:
        parser.exit(2,'PRECHECK FAILED — no files modified: '+str(exc)+'\n')
    diff=''.join(''.join(difflib.unified_diff(
        old[path].splitlines(keepends=True),new[path].splitlines(keepends=True),
        fromfile=('a/'+path if old[path] else '/dev/null'),
        tofile='b/'+path)) for path in sorted(new))
    if args.print_diff:
        print(diff)
    print('Preflight OK —',len(new),'differential files:')
    for path in new:print(' ',path)
    if args.dry_run:
        print('Dry run complete; no changes written.')
        return
    # Entire edit set and syntax prevalidated before any files are written.
    for path,content in new.items():
        dst=repo/path
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text(content,encoding='utf-8')
    artifact=repo/'v13_3_changes.patch'
    artifact.write_text(diff,encoding='utf-8')
    print('Wrote a local review patch:',artifact)
    print('No remote operations have been performed.')

if __name__=='__main__':
    main()
