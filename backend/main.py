"""Vercel Services FastAPI backend: durable evaluation workspace v4."""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app import domain, persistence as db, scheduling
from app.hermes_runtime import chat

app = FastAPI(title='Hermes Agent Evaluation Lab', version='4.0.0', docs_url=None, redoc_url=None)

@app.middleware('http')
async def private_api_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response

MAX_TURNS = 14
COOKIE = 'hermes_lab_session'
PROJECT_ZONE = ZoneInfo('America/Lima')


class LoginInput(BaseModel):
    role: str
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
    if not session:
        raise HTTPException(401, 'Please sign in to the demo.')
    return session


def csrf_for(request: Request) -> str:
    # Header must match server-issued value. Cookie is HttpOnly and same-site.
    token=request.cookies.get(COOKIE,'')
    secret=os.environ.get('DEMO_SIGNING_SECRET','')
    if not secret or len(secret)<32:
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
    return {'ok':True,'agent':'Nous Research Hermes','version':'4.0.0',
            'model':os.getenv('MODEL_NAME','openai/gpt-4o-mini'),
            'configured':bool(os.getenv('OPENROUTER_API_KEY')),
            'database_mode':'turso' if os.getenv('TURSO_DATABASE_URL') else ('unconfigured' if os.getenv('VERCEL') else 'local_sqlite')}


@app.get('/api/catalog')
def catalog():
    return {**domain.read_catalog(),'project_timezone':'America/Lima'}


@app.post('/api/auth/login')
def login(body: LoginInput, request: Request, response: Response):
    if body.role == 'administrator':
        expected=os.environ.get('DEMO_ADMIN_PASSWORD','')
        if len(expected)<16:
            raise HTTPException(503,'A strong DEMO_ADMIN_PASSWORD (16+ characters) is required.')
    elif body.role == 'evaluator':
        expected=os.environ.get('DEMO_ACCESS_CODE','')
        if not expected:raise HTTPException(503,'DEMO_ACCESS_CODE is required for evaluator access.')
    else:
        raise HTTPException(400,'Unknown demo role.')
    # Constant-time comparison. Rate-limit the public route at the edge for internet deployment.
    if not hmac.compare_digest(body.credential,expected):
        raise HTTPException(401,'Invalid demo credentials.')
    csrf_for_value('preflight')  # Fail before writing a session when signing is misconfigured.
    init_db()
    token,info=db.session_create(body.role)
    secure=bool(os.getenv('VERCEL')) or request.url.scheme=='https'
    response.set_cookie(COOKIE,token,max_age=db.SESSION_DAYS*86400,
                        httponly=True,secure=secure,samesite='lax',path='/')
    return {**info,'csrf_token':csrf_for_value(token),'signed_in':True}


def csrf_for_value(token: str) -> str:
    secret=os.environ.get('DEMO_SIGNING_SECRET','')
    if len(secret)<32:raise HTTPException(503,'DEMO_SIGNING_SECRET must have at least 32 characters.')
    return hmac.new(secret.encode(),('csrf:'+token).encode(),hashlib.sha256).hexdigest()


@app.get('/api/me')
def current_user(request: Request, auth: dict = Depends(auth_session)):
    return {**auth,'csrf_token':csrf_for(request)}


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, auth: dict = Depends(checked_session)):
    db.session_revoke(request.cookies.get(COOKIE,''))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@app.get('/api/leads')
def leads(auth: dict=Depends(auth_session)):
    return {'leads':db.list_leads(auth)}


@app.post('/api/leads',status_code=201)
def create_lead(body: LeadInput, auth: dict=Depends(checked_session)):
    params={key:getattr(body,key).strip() for key in ('first_name','last_name','email','phone')}
    try:rec=db.create_lead(auth,**params)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return _public_record(rec)


@app.get('/api/leads/{lead_id}')
def get_lead(lead_id: uuid.UUID, auth: dict=Depends(auth_session)):
    return _public_record(_get_lead(str(lead_id),auth))


@app.post('/api/leads/{lead_id}/chat')
def interact(lead_id: uuid.UUID, body: MessageInput, auth: dict=Depends(checked_session)):
    lead_id=str(lead_id)
    rec=_get_lead(lead_id,auth)
    state=copy.deepcopy(rec['state'])
    if state['turn_count']>=MAX_TURNS:
        raise HTTPException(429,'This lead has reached the 14-turn demo limit.')
    # Only server-side persisted continuation is trusted; browsers submit message text only.
    try:
        domain.begin_turn(state,body.message.strip())
        occupied=db.occupied_by_advisor()
        result=chat(body.message.strip(),rec['hermes_history'],state,extra_occupied=occupied)
        domain.end_turn(state,result['reply'])
        state['turn_count']+=1
        updated=db.save_turn(auth,lead_id,rec['revision'],state,result['history'][-90:],body.message.strip(),result['reply'])
    except db.Conflict as exc:
        raise HTTPException(409,str(exc)) from exc
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('Agent turn failed: %s',type(exc).__name__)
        raise HTTPException(503,'AI advisor temporarily unavailable. This turn was not saved; please retry.') from exc
    return {'reply':result['reply'],'lead':_public_record(updated),'max_turns':MAX_TURNS}


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
