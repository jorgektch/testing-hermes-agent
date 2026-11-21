#!/usr/bin/env python3
"""Apply Hermes v13.3.1 INTEGRATION corrections on v13.3 (no remote actions).

The v13.3 commit copied property_options.py and tests but did not execute its
installer against the production modules. This patch completes those links,
produces a compact SMS menu, and preserves selections in commercial state.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import subprocess
from pathlib import Path

import installer_core as base

ROOT=Path(__file__).resolve().parent
BASE_COMMIT='ce58c928bacbf7621f252aaceb3acac4e06f9ca5'
PatchError=base.PatchError


def commercial(s:str)->str:
    out=base.commercial(s)
    original="    if property_options.is_option_selection(text):\n        return 'property_selection'\n"
    revised=("    if property_options.is_option_selection(\n"
             "            text, has_visible_options=bool(\n"
             "                (state.get('last_presented_property_options') or {}).get('options'))):\n"
             "        return 'property_selection'\n")
    if out.count(original)!=1:
        raise PatchError('commercial_turn: expected state-aware intent anchor missing')
    return out.replace(original,revised)


def context(s:str)->str:
    out=base.context(s)
    old='    if property_options.is_option_selection(text):\n        selected=property_options.resolve_selection(state,text)\n'
    new=("    if property_options.is_option_selection(\n"
         "            text, has_visible_options=bool(\n"
         "                (state.get('last_presented_property_options') or {}).get('options'))):\n"
         "        selected=property_options.resolve_selection(state,text)\n")
    if out.count(old)!=1: raise PatchError('conversation_context: missing option resolution anchor')
    return out.replace(old,new)


def domain(s:str)->str:
    out=base.domain(s)
    old="    state['_turn_intent']=commercial_turn.intent(message)\n"
    if out.count(old)!=1: raise PatchError('domain: missing turn-intent anchor')
    return out.replace(old,"    state['_turn_intent']=commercial_turn.intent(message,state=state)\n")


def hermes(s:str)->str:
    out=base.hermes(s)
    marker='- Present one match WITHOUT an ordinal number. For two or more matches,\n'
    if out.count(marker)!=1: raise PatchError('Hermes prompt: missing v13.3 anchor')
    return out.replace(marker,
        '- Keep exploration listings SHORT, with real newline-separated options, e.g.\n'
        '  "1. H-204 (3 beds, $295k)" and "2. H-101 (3 beds, $320k)".\n'
        '  Reserve detailed descriptions and media for the Details phase.\n'+marker)


def _exact_file(path:str, previous:str, current:str, source:str)->str:
    prior=(ROOT/previous).read_text(encoding='utf-8')
    if source!=prior:
        raise PatchError(path+': source differs from reviewed v13.3 baseline; no overwrite')
    return (ROOT/current).read_text(encoding='utf-8')


def property_options(s:str)->str:
    return _exact_file('backend/app/property_options.py','property_options_base.py',
                       'property_options_modified.py',s)


def regression_tests(s:str)->str:
    return _exact_file('backend/tests/test_v13_3_option_selection.py',
                       'test_v13_3_option_selection_base.py',
                       'backend/tests/test_v13_3_option_selection.py',s)


def readme(s:str)->str:
    old='### Selección de opciones (v13.3)\n'
    if s.count(old)!=1: raise PatchError('README: missing v13.3 section')
    new='''### Selección de opciones (v13.3.1 · integración completa)

El módulo `property_options` funciona ahora **dentro** del ciclo del agente:
`commercial_turn` detecta elecciones ordinales antes que búsquedas;
`conversation_context` las resuelve contra el menú entregado; `domain`
actualiza el foco a **Details** sin cambiar preferencias ni `visit_draft`;
`delivery_policy` repara menús/precios erróneos desde el catálogo;
`hermes_runtime` recibe el contexto validado; `sms` preserva las líneas.

- **Exploration:** una coincidencia aparece sin número. Varias se listan,
  por ejemplo `1. H-204 (3 beds, $295k)` y `2. H-101 (3 beds, $320k)`,
  en líneas separadas con una sola pregunta final. Los detalles van después.
- **Details:** «The second option», «Option 2», «2» mientras existe un menú,
  «H-101» o una referencia inequívoca al precio seleccionan la propiedad
  presentada. No se ejecuta otra búsqueda ni se inicia una reserva.
- **Persistencia:** `last_presented_property_options` contiene la revisión,
  ID del mensaje y orden de las unidades realmente mostradas. Se guarda junto
  al foco `conversation_focus`, `property_interests`, búsqueda y mensajes en
  Turso mediante la transacción normal `end_turn` / `save_turn`. Una selección
  no cambia automáticamente dormitorios, presupuesto o cita pendiente.
- **Hermes y modelo:** interpretan el diálogo y deciden qué datos explicar.
  El backend controla identidad, estado, catálogo, consentimiento y formato
  mínimo de resultados, sin convertir una selección en otra búsqueda.

'''
    start=s.index(old)
    end=s.find('\n## ',start+len(old))
    if end<0: end=len(s)
    return s[:start]+new+s[end:]


from v1331_transforms import commercial as actual_commercial, context as actual_context, domain as actual_domain, policy as actual_policy

TRANSFORMS={
    'backend/app/commercial_turn.py':actual_commercial,
    'backend/app/conversation_context.py':actual_context,
    'backend/app/domain.py':actual_domain,
    'backend/app/delivery_policy.py':actual_policy,
    'backend/app/hermes_runtime.py':hermes,
    'backend/app/sms.py':base.sms,
    'frontend/src/app/globals.css':base.styles,
    'README.md':readme,
    'backend/app/property_options.py':property_options,
    'backend/tests/test_v13_3_option_selection.py':regression_tests,
}


def transform(repo:Path):
    if not (repo/'backend/app/commercial_continuity.py').is_file():
        raise PatchError('Requires the existing Hermes v13.3 checkout')
    old,new={},{}
    for rel,fn in TRANSFORMS.items():
        src=repo/rel
        if not src.is_file(): raise PatchError('Missing '+rel)
        before=src.read_text(encoding='utf-8')
        after=fn(before)
        if after==before: raise PatchError(rel+': transformation produced no changes')
        if rel.endswith('.py'):
            try: ast.parse(after,filename=rel)
            except SyntaxError as exc: raise PatchError(rel+': generated invalid Python '+str(exc)) from exc
        old[rel],new[rel]=before,after
    return old,new


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True)
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--print-diff',action='store_true')
    ap.add_argument('--no-git-check',action='store_true',help=argparse.SUPPRESS)
    a=ap.parse_args()
    repo=a.repo.resolve()
    if not a.no_git_check:
        head=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,text=True,capture_output=True)
        if head.returncode or head.stdout.strip()!=BASE_COMMIT:
            ap.exit(2,'PRECHECK FAILED: expected revision '+BASE_COMMIT+'; got '+head.stdout.strip()+'\n')
        status=subprocess.run(['git','status','--porcelain'],cwd=repo,text=True,capture_output=True)
        if status.returncode or status.stdout.strip():
            ap.exit(2,'PRECHECK FAILED: uncommitted local changes; commit or stash first.\n')
    try: old,new=transform(repo)
    except (PatchError,OSError) as exc: ap.exit(2,'PRECHECK FAILED: '+str(exc)+'\n')
    diff=''.join(''.join(difflib.unified_diff(
        old[p].splitlines(keepends=True),new[p].splitlines(keepends=True),
        fromfile='a/'+p,tofile='b/'+p)) for p in sorted(new))
    if a.print_diff: print(diff)
    print('Preflight OK:',len(new),'files; based on',BASE_COMMIT)
    for p in new: print(' ',p)
    if a.dry_run:
        print('DRY RUN: no files modified.')
        return
    written=[]
    try:
        for p in new:
            (repo/p).write_text(new[p],encoding='utf-8')
            written.append(p)
        (repo/'v13_3_1_changes.patch').write_text(diff,encoding='utf-8')
    except OSError:
        for p in written:
            try:(repo/p).write_text(old[p],encoding='utf-8')
            except OSError:pass
        raise
    print('Installed local changes; inspection patch: v13_3_1_changes.patch')
    print('No GitHub, Vercel or deployment operations performed.')

if __name__=='__main__': main()
