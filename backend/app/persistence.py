"""Durable demo state: SQLite locally, remote Turso/libSQL on Vercel.

One authoritative database for leads, conversations and advisor-wide occupancy.
The UI never supplies executable Hermes history or meeting details.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
LIMA = ZoneInfo('America/Lima')
ACTIVE = {'scheduled', 'confirmed', 'in_progress'}
ROOT = Path(__file__).resolve().parents[2]
SESSION_DAYS = 7
_INITIALIZED: set[str] = set()
_INIT_LOCK = threading.RLock()


class Conflict(Exception):
    """Optimistic revision or occupancy conflict."""


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def as_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(UTC)


def _rows(cur) -> list[dict]:
    columns = [c[0] for c in cur.description or []]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _one(cur) -> dict | None:
    found = _rows(cur)
    return found[0] if found else None


def _db_path() -> str:
    url = os.environ.get('DEMO_DATABASE_PATH') or str(ROOT / 'data' / 'hermes_demo.sqlite3')
    if url.startswith('sqlite:///'):
        url = url[len('sqlite:///'):]
    return url


def connect():
    remote = os.environ.get("TURSO_DATABASE_URL", "").strip()

    if remote:
        token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

        if not token:
            raise RuntimeError(
                "TURSO_AUTH_TOKEN is required with TURSO_DATABASE_URL"
            )

        import libsql

        con = libsql.connect(
            database=remote,
            auth_token=token,
        )
    else:
        if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            raise RuntimeError(
                "Persistent database required: configure Turso credentials"
            )

        path = _db_path()

        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        con = sqlite3.connect(
            path,
            timeout=15,
            check_same_thread=False,
        )
        con.execute("PRAGMA busy_timeout = 15000")

    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextlib.contextmanager
def database():
    con = connect()
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


SCHEMA = [
    '''CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS app_sessions (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, role TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, revoked_at TEXT)''',
    '''CREATE TABLE IF NOT EXISTS leads (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
        first_name TEXT NOT NULL, last_name TEXT NOT NULL, email TEXT NOT NULL,
        phone TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
    '''CREATE INDEX IF NOT EXISTS idx_leads_owner ON leads(owner_id, updated_at)''',
    '''CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        state_json TEXT NOT NULL, hermes_history_json TEXT NOT NULL DEFAULT '[]',
        revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)''',
    '''CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)''',
    '''CREATE TABLE IF NOT EXISTS lead_facts (
        id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        field TEXT NOT NULL, value_json TEXT NOT NULL, evidence TEXT,
        source TEXT NOT NULL, source_message_id TEXT, captured_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS property_interests (
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        unit_id TEXT NOT NULL, status TEXT NOT NULL, source_message_id TEXT,
        updated_at TEXT NOT NULL, PRIMARY KEY (lead_id, unit_id))''',
    '''CREATE TABLE IF NOT EXISTS appointment_offers (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        payload_json TEXT NOT NULL, status TEXT NOT NULL, expires_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS meetings (
        id TEXT PRIMARY KEY, lead_id TEXT REFERENCES leads(id) ON DELETE CASCADE,
        conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
        advisor_id TEXT NOT NULL, unit_id TEXT, starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL, occupied_until TEXT NOT NULL,
        status TEXT NOT NULL, replacement_of TEXT, source TEXT NOT NULL,
        created_at TEXT NOT NULL)''',
    '''CREATE INDEX IF NOT EXISTS idx_meetings_time ON meetings(starts_at, advisor_id)''',
    '''CREATE TABLE IF NOT EXISTS meeting_occupancy (
        advisor_id TEXT NOT NULL, bucket_utc INTEGER NOT NULL,
        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        PRIMARY KEY(advisor_id, bucket_utc))''',
    '''CREATE INDEX IF NOT EXISTS idx_occupancy_meeting ON meeting_occupancy(meeting_id)''',
]


def _occupancy_buckets(start: datetime, until: datetime) -> range:
    """15-minute UTC buckets, including unaligned starts; half-open intervals."""
    first = int(start.timestamp()) // 900
    end = (int(until.timestamp()) + 899) // 900
    return range(first, end)


def _reserve(con, meeting: dict):
    for bucket in _occupancy_buckets(as_dt(meeting['starts_at']), as_dt(meeting['occupied_until'])):
        con.execute('INSERT INTO meeting_occupancy (advisor_id,bucket_utc,meeting_id) VALUES (?,?,?)',
                    (meeting['advisor_id'], bucket, meeting['id']))


def initialize():
    key = os.getenv('TURSO_DATABASE_URL') or _db_path()
    if key in _INITIALIZED:
        return
    with _INIT_LOCK:
        if key in _INITIALIZED:
            return
        _initialize_schema()
        _INITIALIZED.add(key)


def _initialize_schema():
    with database() as con:
        for sql in SCHEMA:
            con.execute(sql)
        marker = _one(con.execute("SELECT value FROM schema_meta WHERE key='fixtures_seeded'"))
        if marker:
            return
        # Seed once, with absolute UTC timestamps. Relative day_offsets never move
        # a booked fixture on a later request or deployment.
        fixture_path = Path(__file__).resolve().parent / 'meetings.json'
        fixtures = json.loads(fixture_path.read_text())['demo_existing_meetings']
        today = datetime.now(LIMA).date()
        for fixture in fixtures:
            day = today + timedelta(days=fixture['day_offset'])
            local = datetime.fromisoformat(f"{day.isoformat()}T{fixture['local_start_time']}").replace(tzinfo=LIMA)
            start = local.astimezone(UTC)
            end = start + timedelta(minutes=fixture['duration_minutes'])
            until = end + timedelta(minutes=fixture.get('buffer_after_minutes', 15))
            meeting = dict(id=fixture['id'], lead_id=None, conversation_id=None,
                           advisor_id=fixture['assigned_sales_user_id'], unit_id=None,
                           starts_at=iso(start), ends_at=iso(end), occupied_until=iso(until),
                           status='confirmed', replacement_of=None, source='synthetic_fixture', created_at=iso())
            con.execute('''INSERT OR IGNORE INTO meetings
               (id,lead_id,conversation_id,advisor_id,unit_id,starts_at,ends_at,occupied_until,status,replacement_of,source,created_at)
               VALUES (:id,:lead_id,:conversation_id,:advisor_id,:unit_id,:starts_at,:ends_at,:occupied_until,:status,:replacement_of,:source,:created_at)''', meeting)
            _reserve(con, meeting)
        con.execute("INSERT INTO schema_meta(key,value) VALUES('fixtures_seeded',?)", (iso(),))


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_create(role: str) -> tuple[str, dict]:
    owner_id = 'administrator' if role == 'administrator' else str(uuid.uuid4())
    token = secrets.token_urlsafe(36)
    created = datetime.now(UTC)
    session = {'id': str(uuid.uuid4()), 'role': role, 'owner_id': owner_id,
               'expires_at': iso(created + timedelta(days=SESSION_DAYS))}
    with database() as con:
        con.execute('''INSERT INTO app_sessions(id,owner_id,role,token_hash,created_at,expires_at)
                       VALUES(?,?,?,?,?,?)''',
                    (session['id'], owner_id, role, _sha(token), iso(created), session['expires_at']))
    return token, session


def session_lookup(token: str | None) -> dict | None:
    if not token:
        return None
    with database() as con:
        return _one(con.execute('''SELECT id,role,owner_id,expires_at FROM app_sessions
             WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?''', (_sha(token),iso())))


def session_revoke(token: str):
    with database() as con:
        con.execute('UPDATE app_sessions SET revoked_at=? WHERE token_hash=?', (iso(), _sha(token)))


def _visibility(role: str, owner_id: str) -> tuple[str, tuple]:
    return ('', ()) if role == 'administrator' else (' AND l.owner_id=?', (owner_id,))


def list_leads(auth: dict) -> list[dict]:
    where,args=_visibility(auth['role'],auth['owner_id'])
    with database() as con:
        return _rows(con.execute('''SELECT l.id,l.first_name,l.last_name,l.email,l.phone,l.created_at,l.updated_at,
                     c.id conversation_id,c.revision,
                     (SELECT content FROM messages m WHERE m.conversation_id=c.id ORDER BY m.created_at DESC,rowid DESC LIMIT 1) last_message
                    FROM leads l JOIN conversations c ON c.lead_id=l.id WHERE 1=1'''+where+
                   ' ORDER BY l.updated_at DESC LIMIT 100',args))


def lead_get(auth: dict, lead_id: str) -> dict | None:
    where,args=_visibility(auth['role'],auth['owner_id'])
    with database() as con:
        rec = _one(con.execute('''SELECT l.*,c.id conversation_id,c.state_json,c.hermes_history_json,c.revision
                         FROM leads l JOIN conversations c ON c.lead_id=l.id WHERE l.id=?'''+where,
                         (lead_id,*args)))
        if not rec:return None
        messages=_rows(con.execute('SELECT id,role,content,created_at FROM messages WHERE conversation_id=? ORDER BY created_at,rowid', (rec['conversation_id'],)))
    rec['state']=json.loads(rec.pop('state_json'))
    rec['hermes_history']=json.loads(rec.pop('hermes_history_json'))
    rec['messages']=messages
    return rec


def create_lead(auth: dict, first_name: str, last_name: str, email: str, phone: str) -> dict:
    from . import domain
    if not all(isinstance(v,str) and v.strip() for v in (first_name,last_name,email,phone)):
        raise ValueError('All four contact fields are required.')
    import re
    for label, value in (('first name', first_name), ('last name', last_name)):
        if not re.fullmatch(r"[A-Za-zÀ-ÿ]+(?:[ '-][A-Za-zÀ-ÿ]+)*", value.strip()):
            raise ValueError(f'Invalid {label}: use letters, spaces, apostrophes or hyphens.')
    state=domain.blank_state()
    # Registration is structured input, not a statement inferred by the model.
    updated=domain.update_lead(state,first_name=first_name,last_name=last_name,email=email,phone=phone)
    if updated.get('error'):raise ValueError(updated['error'])
    lead_id=state['bp_episode']['ids']['lead_id']
    conv_id=state['bp_episode']['ids']['conversation_id']
    project_id=state['bp_episode']['ids']['project_id']
    greeting=f"Hi {first_name}! I'm Ava, your AI advisor for Residencial Jardines del Sol. What budget are you considering, and when would you like to buy?"
    message_id=str(uuid.uuid4())
    state['bp_episode']['sales_messages']=[]
    with database() as con:
        con.execute('''INSERT INTO leads(id,owner_id,project_id,first_name,last_name,email,phone,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',
                    (lead_id,auth['owner_id'],project_id,first_name,last_name,email,phone,iso(),iso()))
        con.execute('''INSERT INTO conversations(id,lead_id,state_json,created_at,updated_at)
                       VALUES(?,?,?,?,?)''',
                    (conv_id,lead_id,json.dumps(state,ensure_ascii=False),iso(),iso()))
        con.execute('INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',
                    (message_id,conv_id,'assistant',greeting,iso()))
    return lead_get(auth, lead_id)


def _booking_rows(state: dict, lead_id: str, conversation_id: str) -> list[dict]:
    rows=[]
    bp=state['bp_episode']
    previous_by_id={m['id']:m for m in bp.get('meetings',[])}
    for booked in state.get('booked_visits',[]):
        meeting=previous_by_id.get(booked['meeting_id'])
        if not meeting:continue
        start=as_dt(meeting['meeting_time'])
        end=start+timedelta(minutes=meeting['duration_minutes'])
        until=end+timedelta(minutes=meeting.get('buffer_after_minutes',15))
        rows.append({'id':meeting['id'],'lead_id':lead_id,'conversation_id':conversation_id,
                 'advisor_id':meeting['assigned_sales_user_id'], 'unit_id':booked.get('unit_id'),
                 'starts_at':iso(start),'ends_at':iso(end),'occupied_until':iso(until),
                 'status':meeting['status'], 'replacement_of':None,'source':'hermes_agent',
                 'created_at':meeting.get('created_at',iso())})
    operations=bp.get('sales_reschedule_operations',[])
    replacements={m.get('replacement_meeting_id'):m.get('original_meeting_id') for m in operations}
    for row in rows:row['replacement_of']=replacements.get(row['id'])
    return rows


def _sync_meetings(con, state: dict, lead_id: str, conversation_id: str):
    existing=_rows(con.execute('SELECT id,status FROM meetings WHERE lead_id=?',(lead_id,)))
    prev={row['id']:row['status'] for row in existing}
    incoming=_booking_rows(state,lead_id,conversation_id)
    # Release cancelled/rescheduled occupancy BEFORE allocating new intervals.
    for row in incoming:
        if row['id'] in prev and row['status'] not in ACTIVE:
            con.execute('DELETE FROM meeting_occupancy WHERE meeting_id=?',(row['id'],))
            con.execute('UPDATE meetings SET status=? WHERE id=?',(row['status'],row['id']))
    for row in incoming:
        if row['id'] in prev:continue
        con.execute('''INSERT INTO meetings
          (id,lead_id,conversation_id,advisor_id,unit_id,starts_at,ends_at,occupied_until,status,replacement_of,source,created_at)
          VALUES (:id,:lead_id,:conversation_id,:advisor_id,:unit_id,:starts_at,:ends_at,:occupied_until,:status,:replacement_of,:source,:created_at)''', row)
        if row['status'] in ACTIVE:
            try:_reserve(con,row)
            except Exception as exc:
                raise Conflict('Selected advisor time was just booked in another session. Ask Hermes for new availability.') from exc


def save_turn(auth: dict, lead_id: str, old_revision: int, state: dict,
              hermes_history: list, user_message: str, assistant_reply: str):
    """Atomic state, messages and cross-lead appointment collision check."""
    where,args=_visibility(auth['role'],auth['owner_id'])
    conv_id=state['bp_episode']['ids']['conversation_id']
    new_state=dict(state)
    new_state['bp_episode']=dict(state['bp_episode'])
    # Messages are persisted separately. Keep the Hermes continuation ONLY in hermes_history_json.
    new_state['bp_episode']['sales_messages']=[]
    with database() as con:
        old = _one(con.execute('''SELECT c.revision,c.id FROM conversations c JOIN leads l ON l.id=c.lead_id
                         WHERE l.id=? AND c.id=?'''+where,(lead_id,conv_id,*args)))
        if not old or old['revision'] != old_revision:
            raise Conflict('This lead conversation changed in another tab. Reload and retry.')
        _sync_meetings(con,state,lead_id,conv_id)
        cur=con.execute('''UPDATE conversations SET revision=revision+1,state_json=?,hermes_history_json=?,updated_at=?
                             WHERE id=? AND revision=?''',
                        (json.dumps(new_state,ensure_ascii=False),json.dumps(hermes_history,ensure_ascii=False),iso(),conv_id,old_revision))
        if cur.rowcount != 1:raise Conflict('Concurrent conversation update detected.')
        con.execute('UPDATE leads SET updated_at=? WHERE id=?',(iso(),lead_id))
        # Preserve the exact IDs from domain.begin_turn/end_turn so each
        # recorded sales fact's source_ref.message_id resolves to this table.
        turn_messages=state['bp_episode']['sales_messages'][-2:]
        if (len(turn_messages)!=2 or turn_messages[0]['role']!='user' or turn_messages[1]['role']!='assistant'
                or turn_messages[0]['content']!=user_message or turn_messages[1]['content']!=assistant_reply):
            raise Conflict('Conversation evidence does not match the messages being saved.')
        for msg in turn_messages:
            con.execute('INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',
                        (msg['id'],conv_id,msg['role'],msg['content'],msg['created_at']))
        sales=state['bp_episode']['leads']['lead_profile_data']['sales_profile']
        facts=list(sales['current'].values())+sales.get('history',[])
        for fact in facts:
            con.execute('''INSERT OR IGNORE INTO lead_facts(id,lead_id,field,value_json,evidence,source,source_message_id,captured_at)
                 VALUES(?,?,?,?,?,?,?,?)''',(fact['id'],lead_id,fact['field'],json.dumps(fact['value'],ensure_ascii=False),
                    fact.get('evidence'),fact.get('source','conversation'),(fact.get('source_ref') or {}).get('message_id'),fact['captured_at']))
        con.execute('DELETE FROM property_interests WHERE lead_id=?',(lead_id,))
        for interest in state.get('property_interests',[]):
            con.execute('INSERT INTO property_interests(lead_id,unit_id,status,source_message_id,updated_at) VALUES(?,?,?,?,?)',
                        (lead_id,interest['unit_id'],interest['status'],interest.get('source_message_id'),iso()))
        proposal=state.get('booking_proposal')
        if proposal:
            con.execute('''INSERT OR REPLACE INTO appointment_offers(id,conversation_id,payload_json,status,expires_at)
                         VALUES(?,?,?,?,?)''',(proposal['proposal_id'],conv_id,json.dumps(proposal),proposal['status'],proposal['expires_at']))
    return lead_get(auth,lead_id)


def occupied_by_advisor() -> dict[str,list[tuple[datetime,datetime]]]:
    now=iso(datetime.now(UTC)-timedelta(days=1))
    with database() as con:
        rows=_rows(con.execute('''SELECT advisor_id,starts_at,occupied_until FROM meetings
                      WHERE status IN ('scheduled','confirmed','in_progress') AND occupied_until>?''',(now,)))
    result:dict[str,list[tuple[datetime,datetime]]]={}
    for m in rows:
        result.setdefault(m['advisor_id'],[]).append((as_dt(m['starts_at']),as_dt(m['occupied_until'])))
    return result


def meeting_list(start: datetime, end: datetime, auth: dict) -> list[dict]:
    with database() as con:
        rows=_rows(con.execute('''SELECT m.id,m.lead_id,m.advisor_id,m.unit_id,m.starts_at,m.ends_at,m.occupied_until,
                    m.status,m.source,l.first_name,l.last_name,l.owner_id
                    FROM meetings m LEFT JOIN leads l ON l.id=m.lead_id
                    WHERE m.starts_at<? AND m.occupied_until>? ORDER BY m.starts_at''',(iso(end),iso(start))))
    for row in rows:
        if auth['role']!='administrator' and row.get('owner_id')!=auth['owner_id']:
            row['lead_id']=None
            row['unit_id']=None
            row['first_name']=None
            row['last_name']=None
    return rows


def delete_lead(auth: dict, lead_id: str) -> bool:
    if auth['role']!='administrator':return False
    with database() as con:
        found=_one(con.execute('SELECT id FROM leads WHERE id=?',(lead_id,)))
        if not found:return False
        # Explicit dependency deletion is portable even when remote libSQL FK PRAGMAs differ.
        ids=[x['id'] for x in _rows(con.execute('SELECT id FROM meetings WHERE lead_id=?',(lead_id,)))]
        for mid in ids:
            con.execute('DELETE FROM meeting_occupancy WHERE meeting_id=?',(mid,))
        con.execute('DELETE FROM meetings WHERE lead_id=?',(lead_id,))
        conv_ids=[x['id'] for x in _rows(con.execute('SELECT id FROM conversations WHERE lead_id=?',(lead_id,)))]
        for cid in conv_ids:
            con.execute('DELETE FROM appointment_offers WHERE conversation_id=?',(cid,))
            con.execute('DELETE FROM messages WHERE conversation_id=?',(cid,))
        con.execute('DELETE FROM property_interests WHERE lead_id=?',(lead_id,))
        con.execute('DELETE FROM lead_facts WHERE lead_id=?',(lead_id,))
        con.execute('DELETE FROM conversations WHERE lead_id=?',(lead_id,))
        con.execute('DELETE FROM leads WHERE id=?',(lead_id,))
        # Sessions contain only identity/role; lead-specific access is revoked by deletion.
        # Deleting owner-specific sessions also revokes open evaluator browser tabs.
        # Admin sessions remain available for deleting other leads.
    return True
