"""Vercel Services FastAPI entrypoint for the demo."""
from __future__ import annotations

import copy
import hmac
import os
import uuid

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app import domain, security
from app.hermes_runtime import chat

app = FastAPI(title='Hermes Real Estate Agent Lab', version='1.0.0', docs_url=None, redoc_url=None)
MAX_TURNS = 14


class ChatRequest(BaseModel):
    token: str = Field(min_length=20, max_length=80_000)
    message: str = Field(min_length=1, max_length=2_000)
    history: list[dict] = Field(default_factory=list, max_length=120)


def check_access(code: str | None) -> None:
    required = os.environ.get('DEMO_ACCESS_CODE', '').strip()
    if required and not hmac.compare_digest(required, code or ''):
        raise HTTPException(401, 'Enter the demo access code to start chatting.')


@app.get('/api/health')
def health():
    return {'ok': True, 'agent': 'Nous Research Hermes',
            'model': os.getenv('MODEL_NAME', 'openai/gpt-4o-mini'),
            'configured': bool(os.getenv('OPENROUTER_API_KEY'))}


@app.get('/api/catalog')
def catalog():
    data = domain.read_catalog()
    return {**data, 'project_timezone': 'America/Lima'}


@app.post('/api/session')
def start_session(x_demo_access_code: str | None = Header(default=None)):
    check_access(x_demo_access_code)
    try:
        state = domain.blank_state()
        payload = {'session_id': str(uuid.uuid4()), 'state': state}
        token = security.sign(payload)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {'session_id': payload['session_id'], 'state': state,
            'token': token, 'max_turns': MAX_TURNS}


@app.post('/api/chat')
def interact(body: ChatRequest, x_demo_access_code: str | None = Header(default=None)):
    check_access(x_demo_access_code)
    try:
        payload = security.verify(body.token)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    state = copy.deepcopy(payload['state'])
    if state['turn_count'] >= MAX_TURNS:
        raise HTTPException(429, 'This single-use demo session has reached its turn limit.')
    # Bound client-provided history; exclude attempts to inject a new system
    # message. Hermes requires tool messages from previous turns for context.
    history = [item for item in body.history if item.get('role') in {'user', 'assistant', 'tool'}][-65:]
    if len(str(history)) > 150_000:
        raise HTTPException(400, 'Conversation history exceeds demo limits.')
    try:
        result = chat(body.message.strip(), history, state)
    except Exception as exc:
        # Do not return provider error bodies (which might contain secrets).
        import logging
        logging.getLogger(__name__).exception('Agent turn failed: %s', type(exc).__name__)
        raise HTTPException(503, 'The AI advisor is temporarily unavailable. Try again shortly; your session was not charged a turn.') from exc
    state['turn_count'] += 1
    payload['state'] = state
    try:
        new_token = security.sign(payload)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {'reply': result['reply'], 'history': result['history'],
            'state': state, 'token': new_token, 'max_turns': MAX_TURNS}
