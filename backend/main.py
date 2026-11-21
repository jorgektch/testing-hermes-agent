"""Vercel Services FastAPI backend: durable evaluation workspace v6."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import queue
import threading
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import domain, persistence as db, scheduling
from app.hermes_runtime import chat

app = FastAPI(title='Hermes Agent Evaluation Lab', version='8.0.0', docs_url=None, redoc_url=None)

@app.middleware('http')
async def private_api_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response

MAX_TURNS = int(os.environ.get('DEMO_MAX_TURNS', '30'))
COOKIE = 'hermes_lab_session'
PROJECT_ZONE = ZoneInfo('America/Lima')


def allow_weak_demo_credentials() -> bool:
    """Explicit opt-in for synthetic testing ONLY; unsafe on public deployments.

    With this set, anyone who knows the demo credential can act as administrator.
    A short signing secret also weakens CSRF protection. Keep disabled by default.
    """
    return os.environ.get('DEMO_ALLOW_WEAK_CREDENTIALS', '').strip().lower() in ('true', '1', 'yes')


def accepted_demo_credential(value: str, minimum_length: int) -> bool:
    return len(value) >= minimum_length or (allow_weak_demo_credentials() and value == 'demo')


class LoginInput(BaseModel):
    # Only administrator login remains; old clients may send this field, but
    # cannot request or obtain an evaluator session.
    role: str = 'administrator'
    credential: str = Field(min_length=1, max_length=200)


class LeadInput(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=5, max_length=200)
    phone: str = Field(min_length=7, max_length=25)


class MessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def init_db():
    try:db.initialize()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('Database initialization failed: %s', type(exc).__name__)
        raise HTTPException(503, 'The demo database is not configured or unavailable.') from exc


def auth_session(request: Request):
    init_db()
    session = db.session_lookup(request.cookies.get(COOKIE))
    if not session or session['role'] != 'administrator':
        # Existing evaluator sessions are not permitted to continue after
        # switching the application to administrator-only access.
        raise HTTPException(401, 'Please sign in as administrator.')
    return session


def csrf_for(request: Request) -> str:
    # Header must match server-issued value. Cookie is HttpOnly and same-site.
    token=request.cookies.get(COOKIE,'')
    secret=os.environ.get('DEMO_SIGNING_SECRET','')
    if not accepted_demo_credential(secret, 32):
        raise HTTPException(503,'Configure DEMO_SIGNING_SECRET (32+ characters).')
    return hmac.new(secret.encode(),('csrf:'+token).encode(),hashlib.sha256).hexdigest()


def checked_session(request: Request, x_demo_csrf: str | None = Header(default=None)):
    auth=auth_session(request)
    if not x_demo_csrf or not hmac.compare_digest(csrf_for(request),x_demo_csrf):
        raise HTTPException(403,'CSRF verification failed; refresh the session.')
    return auth


def _public_record(record: dict) -> dict:
    safe=dict(record)
    safe.pop('hermes_history',None)
    return safe


def _get_lead(lead_id: str, auth: dict) -> dict:
    rec=db.lead_get(auth,lead_id)
    if not rec:raise HTTPException(404,'Lead not found.')
    return rec


@app.get('/api/health')
def health():
    return {'ok':True,'agent':'Nous Research Hermes','version':'8.0.0',
            'model':os.getenv('MODEL_NAME','openai/gpt-4o-mini'),
            'configured':bool(os.getenv('OPENROUTER_API_KEY')),
            'database_mode':'turso' if os.getenv('TURSO_DATABASE_URL') else ('unconfigured' if os.getenv('VERCEL') else 'local_sqlite')}


@app.get('/api/catalog')
def catalog():
    return {**domain.read_catalog(),'project_timezone':'America/Lima'}


@app.post('/api/auth/login')
def login(body: LoginInput, request: Request, response: Response):
    if body.role != 'administrator':
        raise HTTPException(403, 'Only administrator access is supported.')
    expected=os.environ.get('DEMO_ADMIN_PASSWORD','')
    if not accepted_demo_credential(expected, 16):
        raise HTTPException(503, 'A strong DEMO_ADMIN_PASSWORD (16+ characters) is required.')
    # Constant-time comparison. Rate-limit the public route at the edge for internet deployment.
    if not hmac.compare_digest(body.credential,expected):
        raise HTTPException(401,'Invalid demo credentials.')
    csrf_for_value('preflight')  # Fail before writing a session when signing is misconfigured.
    init_db()
    token,info=db.session_create(body.role)
    secure=bool(os.getenv('VERCEL')) or request.url.scheme=='https'
    response.set_cookie(COOKIE,token,max_age=db.SESSION_DAYS*86400,
                        httponly=True,secure=secure,samesite='lax',path='/')
    return {**info,'csrf_token':csrf_for_value(token),'signed_in':True,'max_turns':MAX_TURNS}


def csrf_for_value(token: str) -> str:
    secret=os.environ.get('DEMO_SIGNING_SECRET','')
    if not accepted_demo_credential(secret, 32):raise HTTPException(503,'DEMO_SIGNING_SECRET must have at least 32 characters.')
    return hmac.new(secret.encode(),('csrf:'+token).encode(),hashlib.sha256).hexdigest()


@app.get('/api/me')
def current_user(request: Request, auth: dict = Depends(auth_session)):
    return {**auth,'csrf_token':csrf_for(request),'max_turns':MAX_TURNS}


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, auth: dict = Depends(checked_session)):
    db.session_revoke(request.cookies.get(COOKIE,''))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@app.get('/api/leads')
def leads(auth: dict=Depends(auth_session)):
    return {'leads':db.list_leads(auth)}


@app.get('/api/admin/storage')
def admin_storage(auth: dict = Depends(auth_session)):
    # Safe metadata only: no database URL, credentials, leads or conversation text.
    return db.storage_diagnostics()


@app.post('/api/leads',status_code=201)
def create_lead(body: LeadInput, auth: dict=Depends(checked_session)):
    params={key:getattr(body,key).strip() for key in ('first_name','last_name','email','phone')}
    try:rec=db.create_lead(auth,**params)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return _public_record(rec)


@app.get('/api/leads/{lead_id}')
def get_lead(lead_id: uuid.UUID, auth: dict=Depends(auth_session)):
    return _public_record(_get_lead(str(lead_id),auth))


def _begin_turn(state: dict, message: str) -> None:
    # Hermes receives the same authoritative in-memory turn state as its tools.
    # This narrow deterministic guard only accepts unambiguous explicit wishes;
    # all other extraction/decisions remain Hermes's domain tool calls.
    domain.begin_turn(state, message)
    verified = domain.verify_explicit_preferences(state, message)
    if verified.get('error'):
        raise ValueError(verified['error'])


def _finish_turn(lead_id: str, rec: dict, state: dict, auth: dict,
                 message: str, *, on_tool_state=None) -> dict:
    occupied = db.occupied_by_advisor()
    options = {'extra_occupied': occupied}
    if on_tool_state is not None:
        options['on_tool_state'] = on_tool_state
    result = chat(message, rec['hermes_history'], state, **options)
    safe_reply = domain.redact_unconfirmed_advisor_names(result['reply'], state)
    domain.end_turn(state, safe_reply)
    state['turn_count'] += 1
    updated = db.save_turn(auth, lead_id, rec['revision'], state,
                           result['history'][-90:], message, safe_reply)
    return {'reply': updated.get('last_assistant_reply', safe_reply), 'lead': _public_record(updated), 'max_turns': MAX_TURNS}


@app.post('/api/leads/{lead_id}/chat')
def interact(lead_id: uuid.UUID, body: MessageInput, auth: dict=Depends(checked_session)):
    lead_id = str(lead_id)
    rec = _get_lead(lead_id, auth)
    state = copy.deepcopy(rec['state'])
    if state['turn_count'] >= MAX_TURNS:
        raise HTTPException(429, 'This lead has reached the demo turn limit.')
    try:
        message = body.message.strip()
        _begin_turn(state, message)
        return _finish_turn(lead_id, rec, state, auth, message)
    except db.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception('Agent turn failed: %s', type(exc).__name__)
        raise HTTPException(503, 'AI advisor temporarily unavailable. This turn was not saved; please retry.') from exc


def _sse(event: str, payload: dict) -> str:
    """Named UTF-8 SSE event; no authentication data or Hermes history leaks."""
    return 'event: ' + event + '\ndata: ' + json.dumps(payload, ensure_ascii=False, default=str) + '\n\n'


@app.post('/api/leads/{lead_id}/chat/stream')
def interact_stream(lead_id: uuid.UUID, body: MessageInput,
                    auth: dict=Depends(checked_session)):
    """POST + CSRF, first-class FastAPI SSE supported by Vercel Python.

    A stream event is provisional until `committed` is emitted. No partial
    conversation, meeting, or lead profile changes persist on Hermes failures.
    Hermes still runs via its documented AIAgent.run_conversation path.
    """
    lead_id = str(lead_id)
    rec = _get_lead(lead_id, auth)
    if rec['state']['turn_count'] >= MAX_TURNS:
        raise HTTPException(429, 'This lead has reached the demo turn limit.')
    message = body.message.strip()
    state = copy.deepcopy(rec['state'])

    def stream():
        try:
            _begin_turn(state, message)
            started = state['bp_episode']['sales_messages'][-1]
            yield _sse('received', {'message_id': started['id'],
                                    'created_at': started['created_at']})
            yield _sse('profile_preview', {'profile': domain.profile_snapshot(state),
                                          'saved': False})
        except Exception:
            logging.getLogger(__name__).exception('Failed preparing streamed Hermes turn')
            yield _sse('error', {'detail': 'Unable to prepare this turn. It was not saved.'})
            return

        events: queue.Queue = queue.Queue()

        def on_tool_state(result):
            events.put(('profile_preview', {'profile': result['profile'],
                                            'source_tool': result['tool'],
                                            'tool_ok': result['ok'], 'saved': False}))

        def worker():
            try:
                payload = _finish_turn(lead_id, rec, state, auth, message,
                                       on_tool_state=on_tool_state)
                events.put(('committed', payload))
            except db.Conflict as exc:
                events.put(('error', {'detail': str(exc), 'status': 409}))
            except Exception as exc:
                logging.getLogger(__name__).exception('Streamed agent turn failed: %s', type(exc).__name__)
                events.put(('error', {'detail': 'AI advisor temporarily unavailable. This turn was not saved.',
                                      'status': 503}))
            finally:
                events.put(('done', {}))

        # Request-scoped worker allows observing Hermes tool calls without
        # patching its agent loop. It is not a detached/background job.
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
        while True:
            try:
                kind, payload = events.get(timeout=10)
            except queue.Empty:
                yield ': keep-alive\n\n'
                continue
            if kind == 'done':
                break
            yield _sse(kind, payload)

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'})


@app.get('/api/calendar')
def calendar(start: str=Query(...,description='YYYY-MM-DD, project timezone'),
             days: int=Query(7,ge=1,le=14),
             advisor_ids: str=Query(''),
             auth: dict=Depends(auth_session)):
    try:
        day=datetime.strptime(start,'%Y-%m-%d').date()
        date_end=day+timedelta(days=days)
    except ValueError as exc:raise HTTPException(422,'Use YYYY-MM-DD for start.') from exc
    today=datetime.now(PROJECT_ZONE).date()
    if day < today-timedelta(days=14) or date_end > today+timedelta(days=25):
        raise HTTPException(422,'Calendar range is outside the demo horizon.')
    project_id=scheduling.policy()['project_id']
    available_staff=scheduling.eligible_users(project_id)
    selected=set(x for x in advisor_ids.split(',') if x)
    allowed={u['demo_advisor_id'] for u in available_staff}
    if not selected:selected=allowed
    if not selected.issubset(allowed):raise HTTPException(422,'Unknown advisor filter.')
    staff=[u for u in available_staff if u['demo_advisor_id'] in selected]
    span_start=datetime.combine(day,datetime.min.time(),tzinfo=PROJECT_ZONE).astimezone(timezone.utc)
    span_end=datetime.combine(date_end,datetime.min.time(),tzinfo=PROJECT_ZONE).astimezone(timezone.utc)
    meetings=db.meeting_list(span_start,span_end,auth)
    external=db.occupied_by_advisor()
    slots=scheduling.available_slots(domain.blank_state(),include_today=True,extra_occupied=external)
    slots=[s for s in slots if day.isoformat()<=s['date']<date_end.isoformat() and s['advisor_id'] in selected]
    visible_ids={u['id'] for u in staff}
    meetings=[m for m in meetings if m['advisor_id'] in visible_ids]
    return {'project_timezone':'America/Lima','server_time_utc':db.iso(),
            'advisors':[{'advisor_id':u['demo_advisor_id'],'user_id':u['id'],
                         'name':u['display_name'],'specialty':u['specialty']} for u in staff],
            'slots':slots,'meetings':meetings,
            'policy':{k:scheduling.policy()[k] for k in ('visit_duration_minutes','buffer_after_minutes','slot_search_step_minutes','booking_horizon_days')},
            'notice':'Synthetic advisor schedules; bookings are shared within the demo database, not Google Calendar.'}


@app.delete('/api/admin/leads/{lead_id}')
def delete_lead(lead_id: uuid.UUID, auth: dict=Depends(checked_session)):
    if auth['role']!='administrator':raise HTTPException(403,'Only an administrator may permanently delete leads.')
    if not db.delete_lead(auth,str(lead_id)):
        raise HTTPException(404,'Lead not found.')
    return {'ok':True,'deleted_lead_id':str(lead_id),'permanent':True}
