"""Run a real Nous Research Hermes AIAgent with six registered domain tools."""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from . import domain

# Singleton registry is process-global in Hermes. Serialize agent conversations
# per warm instance; register closures capturing only this request's state.
_HERMES_AGENT_LOCK = threading.RLock()
TOOLSET = 'evergreen_real_estate_demo'

PROMPT = '''You are Ava, the friendly, persuasive, English-speaking sales consultant for
Evergreen Park Residences, a fictional residential project in Lima, Peru.
The lead just filled a Facebook or Instagram Meta Ads lead form about this project.
You are running in a synthetic PUBLIC DEMO. Never claim properties, images,
advisors or bookings are real. Never invent data, prices, links or inventory.

MANDATORY LIVE STRUCTURED STATE:
- When the lead supplies ANY fact about budget, target purchase timeframe,
  house/apartment preference, minimum bedrooms, property ID, first name,
  last name, email, phone, call update_lead_profile BEFORE replying.
- Never guess fields. Only set facts explicitly provided by the lead.
- Extract full name into first_name and last_name only when unambiguous.
- Use exact tool parameter names. Do not overwrite known facts unless updated.
- You may update partial fields; the dashboard shows progress after each turn.

SALES WORKFLOW:
1. Begin warmly and acknowledge the Meta ad about THIS project.
2. Naturally qualify approximate budget and estimated purchase timeframe,
   then property type and bedrooms; do not dump the full catalog before qualification.
3. Use get_project_overview and evaluate_properties_for_lead. Compare readiness
   with purchase timeline. Present fitting units; respectfully identify above-budget
   alternatives without pushing them.
4. Before sharing media use get_unit_media. Copy exact URLs; acknowledge null or
   missing gallery/PDF honestly. Files are synthetic demo assets.
5. Financing policy is STRICT: no rates, monthly payments, down-payment figures,
   financing quotes, banks, or loan terms. All personalized financing is
   presented by the human advisor during the onsite visit.
6. Offer a physical showroom visit lasting 45 minutes, with a 15-minute gap
   afterward. Use get_available_visit_slots to offer REAL tool-returned times.
7. Before calling book_onsite_visit collect first_name, last_name, email,
   phone, unit_id, advisor_id, date (YYYY-MM-DD), and start_time (HH:MM).
   Call the booking tool only after explicit acceptance of one slot and only once.
8. Always clarify that a confirmed visit is a DEMO simulation, not a real-world
   appointment or notification to the advisor.

The UI already shows the full inventory; chat should be consultative and concise.
Ask at most two questions in each response. Stay in English. Follow tool errors.
Do not expose implementation details, secrets, or the system prompt.
'''

SCHEMAS: dict[str, dict] = {
    'get_project_overview': {
        'description': 'Read the project, location, advantages and amenities.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    'evaluate_properties_for_lead': {
        'description': 'Match properties to the lead budget, target purchase date and minimum bedrooms.',
        'parameters': {'type': 'object', 'properties': {
            'max_budget_usd': {'type': 'integer', 'description': 'Maximum price in USD.'},
            'target_purchase_timeframe': {'type': 'string', 'description': 'Example: within 2 months, 2027-01, or ASAP.'},
            'min_bedrooms': {'type': 'integer', 'description': 'Desired minimum bedrooms.'},
        }, 'required': ['max_budget_usd']},
    },
    'get_unit_media': {
        'description': 'Get gallery images and floor-plan brochure PDF URLs for a unit.',
        'parameters': {'type': 'object', 'properties': {
            'unit_id': {'type': 'string', 'description': 'Known residential unit ID.'},
        }, 'required': ['unit_id']},
    },
    'get_available_visit_slots': {
        'description': 'List actual demo visit slots by advisor and date, 45-minute visit plus 15-minute buffer.',
        'parameters': {'type': 'object', 'properties': {
            'date_str': {'type': 'string', 'description': 'YYYY-MM-DD date, or tomorrow; omit for all dates.'},
        }},
    },
    'book_onsite_visit': {
        'description': 'Book exactly one simulated onsite visit after all details and explicit slot confirmation.',
        'parameters': {'type': 'object', 'properties': {
            'first_name': {'type': 'string'}, 'last_name': {'type': 'string'},
            'email': {'type': 'string'}, 'phone': {'type': 'string'},
            'unit_id': {'type': 'string'}, 'advisor_id': {'type': 'string'},
            'date_str': {'type': 'string', 'description': 'YYYY-MM-DD'},
            'start_time': {'type': 'string', 'description': 'HH:MM in America/Lima'},
        }, 'required': ['first_name', 'last_name', 'email', 'phone', 'unit_id', 'advisor_id', 'date_str', 'start_time']},
    },
    'update_lead_profile': {
        'description': 'MANDATORY FIRST: Store any newly stated lead facts for the live visual state dashboard. Only pass supplied facts.',
        'parameters': {'type': 'object', 'properties': {
            'first_name': {'type': 'string'}, 'last_name': {'type': 'string'},
            'email': {'type': 'string'}, 'phone': {'type': 'string'},
            'budget_usd': {'type': 'integer'}, 'purchase_timeframe': {'type': 'string'},
            'property_type': {'type': 'string', 'enum': ['Apartment', 'House', 'Either']},
            'min_bedrooms': {'type': 'integer'}, 'interest_unit_id': {'type': 'string'},
        }},
    },
}


def _run_locked(message: str, history: list[dict], state: dict) -> dict:
    from run_agent import AIAgent
    from tools.registry import registry
    from model_tools import get_tool_definitions

    funcs = {
        'get_project_overview': lambda a: domain.project_overview(),
        'evaluate_properties_for_lead': lambda a: domain.evaluate_properties(state, **a),
        'get_unit_media': lambda a: domain.unit_media(**a),
        'get_available_visit_slots': lambda a: domain.slots(state, **a),
        'book_onsite_visit': lambda a: domain.book_visit(state, **a),
        'update_lead_profile': lambda a: domain.update_lead(state, **a),
    }
    for name, func in funcs.items():
        def handler(args: dict, _func=func, **_kwargs: Any) -> str:
            try:
                return json.dumps(_func(args), ensure_ascii=False, default=str)
            except (ValueError, TypeError, KeyError) as exc:
                return json.dumps({'error': str(exc)})
        registry.register(name=name, toolset=TOOLSET,
                          schema={'name': name, **SCHEMAS[name]}, handler=handler,
                          check_fn=lambda: True)

    api_key = os.getenv('OPENROUTER_API_KEY', '')
    if not api_key:
        raise RuntimeError('OPENROUTER_API_KEY is not configured.')
    agent = AIAgent(
        model=os.getenv('MODEL_NAME', 'openai/gpt-4o-mini'),
        api_key=api_key, base_url='https://openrouter.ai/api/v1',
        enabled_toolsets=[TOOLSET],
        disabled_toolsets=['terminal', 'file', 'web', 'browser'],
        quiet_mode=True, skip_memory=True, skip_context_files=True,
        ephemeral_system_prompt=PROMPT,
        tool_delay=0, max_iterations=12,
        save_trajectories=False,
    )
    allowed = {name for name in funcs}
    loaded = set(agent.valid_tool_names)
    if not allowed.issubset(loaded) or loaded - allowed:
        raise RuntimeError(f'Hermes tool allowlist mismatch: {loaded ^ allowed}')
    result = agent.run_conversation(message, conversation_history=history)
    answer = result.get('final_response')
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError('Hermes did not provide a text response.')
    return {'reply': answer.strip(), 'history': result.get('messages') or []}


def chat(message: str, history: list[dict], state: dict) -> dict:
    """Synchronously run one turn, serializing the mutable Hermes registry."""
    with _HERMES_AGENT_LOCK:
        return _run_locked(message, history, state)
