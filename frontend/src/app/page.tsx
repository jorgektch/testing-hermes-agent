'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, ArrowLeft, ArrowRight, ArrowUp, BadgeCheck, Bath, BedDouble,
  Bot, CalendarDays, Check, CheckCircle2, ChevronDown, ChevronRight, Clock3,
  FileText, House, Images, Info, KeyRound, Layers3, LockKeyhole,
  MapPin, MessageCircle, ShieldCheck, SlidersHorizontal, Sparkles, Square, Terminal,
  Users, X,
} from 'lucide-react';

type Unit = {
  unit_id: string; property_type: 'Apartment' | 'House'; commercial_name: string;
  bedrooms: number; bathrooms: number; area_sqm: number; price_usd: number;
  available_from_date: string; status: string; highlights: string[];
  media: { gallery_urls: string[]; floorplan_brochure_pdf: string | null };
  property_type_code?: string; property_type_description?: string; floor_number?: number | null;
  lot_area_m2?: number | null; currency?: string; publication_status?: string;
  confirmed_features?: Record<string, string | number | boolean>; demo_inventory_last_updated_at?: string;

};
type Catalog = {
  project_info: { name: string; developer: string; value_proposition: string;
    project_type?: string; development_status?: string; published_revision?: number;
    phase_names?: string[]; inventory_summary?: { total_units: number; available_units: number; reserved_units: number };
    amenities: string[]; general_brochure_pdf: string;
    location: { street_address: string; district: string; city: string; country: string }; };
  properties: Unit[];
  sales_advisors: { advisor_id: string; name: string; specialty: string }[];
};
type Lead = {
  first_name: string | null; last_name: string | null; email: string | null; phone: string | null;
  budget_usd: number | null; purchase_timeframe: string | null; property_type: string | null;
  min_bedrooms: number | null; interest_unit_id: string | null;
};
type Booking = { booking_id: string; advisor_name: string; unit_id: string | null; status?: string; date: string;
  start_time: string; end_time: string; simulation_only: boolean };
type BpFact = { field: string; value: unknown; status: string; source: string; evidence: string; captured_at: string; source_ref?: { message_id?: string } };
type Profile = {
  bp_episode?: { leads: { lead_profile_data: { sales_profile: { current: Record<string, BpFact>; history: BpFact[] } } };
    meetings?: Record<string, unknown>[]; sales_dialogue_states?: { state_json: Record<string,unknown> } };
  lead: Lead; stage: string; shortlisted_unit_ids: string[]; booked_visits: Booking[];
  booking_proposal?: { proposal_id: string; status: string; expires_at: string; options: { advisor_id: string; advisor_name: string; date: string; start_time: string; visit_end_time: string; buffer_end_time: string }[] } | null;
  events: { id: string; event: string; label: string; timestamp: string }[];
  turn_count: number;
};
type Line = { role: 'user' | 'assistant'; content: string; id: string };
type Session = { sessionId: string; token: string; state: Profile;
  history: Record<string, unknown>[]; messages: Line[] };

const STORE_KEY = 'hermes-sales-lab-session-bp-v3';
const ACCESS_KEY = 'hermes-sales-lab-access';
const MAX_TURNS = 14;
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const promptExamples = [
  'My budget is $260,000, and I want to buy within two months.',
  'I need at least two bedrooms. What can you recommend?',
  'Could you show me the gallery and floor plan for A-201?',
];
const greeting: Line = { role: 'assistant', id: 'greeting', content:
  "Hi there! Welcome to Residencial Jardines del Sol. 👋 I'm Ava, your AI sales advisor. I'd love to help you find the right home. What budget are you considering, and when are you hoping to buy?" };
const initial: Profile = {
  lead: { first_name: null, last_name: null, email: null, phone: null, budget_usd: null,
    purchase_timeframe: null, property_type: null, min_bedrooms: null, interest_unit_id: null },
  stage: 'New Meta lead', shortlisted_unit_ids: [], booked_visits: [], events: [], turn_count: 0,
};
const leadFields: { key: keyof Lead; label: string; fallback: string; format?: (v: any) => string }[] = [
  { key: 'budget_usd', label: 'Maximum budget', fallback: 'Not captured', format: (v) => usd.format(v) },
  { key: 'purchase_timeframe', label: 'Purchase timeline', fallback: 'Not captured' },
  { key: 'property_type', label: 'Property type', fallback: 'Not specified' },
  { key: 'min_bedrooms', label: 'Bedrooms', fallback: 'Not specified', format: (v) => `${v}+ bedrooms` },
  { key: 'interest_unit_id', label: 'Interested unit', fallback: 'Not selected' },
  { key: 'first_name', label: 'First name', fallback: 'Not captured' },
  { key: 'last_name', label: 'Last name', fallback: 'Not captured' },
  { key: 'email', label: 'Email address', fallback: 'Not captured' },
  { key: 'phone', label: 'Phone number', fallback: 'Not captured' },
];

function displayReady(v: string) { return v === 'Immediate' ? 'Ready now' : v.split('-').reverse().join('/'); }
function linkify(message: string) {
  const parts = message.split(/(https?:\/\/[^\s<>"']+|\/demo-assets\/[A-Za-z0-9._-]+)/g);
  return parts.map((part, i) => /^(https?:\/\/|\/demo-assets\/)/.test(part)
    ? <a key={i} href={part} className="message-link" target="_blank" rel="noopener noreferrer">{part}<ArrowUp size={12}/></a>
    : <span key={i}>{part}</span>);
}
function eventTime(t: string) {
  try { return new Date(t).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }); }
  catch { return ''; }
}

export default function Page() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [filter, setFilter] = useState<'All' | 'Apartment' | 'House'>('All');
  const [selected, setSelected] = useState<Unit | null>(null);
  const [photo, setPhoto] = useState(0);
  const [showProject, setShowProject] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Line[]>([greeting]);
  const [draft, setDraft] = useState('');
  const [accessCode, setAccessCode] = useState('');
  const [showAccess, setShowAccess] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [profileFlash, setProfileFlash] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const state = session?.state || initial;
  const filled = Object.values(state.lead).filter((x) => x !== null && x !== '').length;
  const book = [...state.booked_visits].reverse().find((v) => v.status === 'confirmed');
  const previousVisits = state.booked_visits.filter((v) => v.status !== 'confirmed');
  const project = catalog?.project_info;
  const properties = catalog?.properties.filter((u) => filter === 'All' || u.property_type === filter) || [];

  useEffect(() => {
    const saved = window.sessionStorage.getItem(STORE_KEY);
    if (saved) {
      try { const data = JSON.parse(saved) as Session;
        if (data.sessionId && data.token && data.state) {
          setSession(data); setMessages(data.messages || [greeting]);
        }
      } catch { window.sessionStorage.removeItem(STORE_KEY); }
    }
    setAccessCode(window.sessionStorage.getItem(ACCESS_KEY) || '');
    fetch('/api/catalog').then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setCatalog).catch((e: Error) => setCatalogError(e.message));
  }, []);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }); }, [messages, busy]);
  useEffect(() => {
    if (!selected) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    modalRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setSelected(null); return; }
      if (event.key !== 'Tab') return;
      const nodes = Array.from(modalRef.current?.querySelectorAll<HTMLElement>('button,a[href],input,textarea,[tabindex]:not([tabindex="-1"])') ?? []).filter(el => !el.hasAttribute('disabled'));
      if (!nodes.length) return;
      const first=nodes[0], last=nodes[nodes.length-1];
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { document.removeEventListener('keydown',onKeyDown); document.body.style.overflow=oldOverflow; previousFocus?.focus(); };
  }, [selected]);

  const save = useCallback((v: Session) => {
    window.sessionStorage.setItem(STORE_KEY, JSON.stringify(v)); setSession(v);
  }, []);
  const send = useCallback(async () => {
    const message = draft.trim();
    if (!message || busy || state.turn_count >= MAX_TURNS) return;
    setBusy(true); setError(''); setDraft('');
    const next: Line[] = [...messages, { role: 'user', content: message, id: `user-${Date.now()}` }];
    setMessages(next);
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (accessCode) headers['X-Demo-Access-Code'] = accessCode;
      let current = session;
      if (!current) {
        const res = await fetch('/api/session', { method: 'POST', headers, body: '{}' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Unable to start session');
        current = { sessionId: data.session_id, token: data.token, state: data.state,
          history: [], messages: [greeting] };
      }
      const res = await fetch('/api/chat', { method: 'POST', headers,
        body: JSON.stringify({ token: current.token, message }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Unable to contact the AI agent');
      const updated: Line[] = [...next, { role: 'assistant', content: data.reply, id: `ai-${Date.now()}` }];
      setMessages(updated);
      save({ sessionId: current.sessionId, token: data.token, state: data.state,
        history: [], messages: updated });
      setProfileFlash(true); window.setTimeout(() => setProfileFlash(false), 1200);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unexpected error');
      setDraft(message); setMessages(messages);
    } finally { setBusy(false); }
  }, [accessCode, busy, draft, messages, save, session, state.turn_count]);

  return <div className="app-shell">
    <aside className="shell-sidebar" aria-label="Demo navigation">
      <div className="sidebar-wordmark"><span>black</span><strong>penguin</strong><small>.ai</small></div>
      <div className="sidebar-divider"/>
      <div className="sidebar-section">RESEARCH WORKSPACE</div>
      <nav className="sidebar-nav">
        <a href="#overview"><span className="material-symbols-outlined">space_dashboard</span>Overview</a>
        <a href="#inventory"><span className="material-symbols-outlined">apartment</span>Property inventory</a>
        <a href="#conversation" className="sidebar-active"><span className="material-symbols-outlined">forum</span>Hermes agent</a>
        <a href="#lead-state"><span className="material-symbols-outlined">person_search</span>Lead intelligence</a>
      </nav>
      <div className="sidebar-section sidebar-section-second">EXPERIMENT</div>
      <div className="sidebar-support"><span className="material-symbols-outlined">science</span><span>Hermes Agent Lab<small>Independent research demo</small></span></div>
      <div className="sidebar-bottom"><div className="sidebar-profile-icon">H</div><span>Sandbox session<small>Synthetic data · No CRM</small></span><span className="material-symbols-outlined">verified_user</span></div>
    </aside>
    <div className="shell-main">
    <header className="global-header">
      <div className="brand"><div className="brand-logo"><span className="material-symbols-outlined">neurology</span></div><div><strong>Hermes <span>Agent Lab</span></strong><small>REAL ESTATE SALES EXPERIMENT</small></div></div>
      <div className="header-right"><div className="model-badge"><span className="green-dot"/> OpenRouter <span className="badge-separator"/> gpt-4o-mini</div>
        <button className="access-button" onClick={() => setShowAccess(!showAccess)}><LockKeyhole size={13}/> {accessCode ? 'Access set' : 'Demo access'} <ChevronDown size={14}/></button>
        <div className="avatar-small">AI</div></div>
      {showAccess && <div className="access-dialog"><div className="eyebrow">PRIVATE DEMO ACCESS</div>
        <p>Enter the optional visitor code. Your OpenRouter API key never enters the browser.</p>
        <div className="access-control"><KeyRound size={15}/><input type="password" value={accessCode} onChange={(e) => setAccessCode(e.target.value)} placeholder="Access code"/><button onClick={() => {window.sessionStorage.setItem(ACCESS_KEY, accessCode); setShowAccess(false);}}>Save</button></div>
      </div>}
    </header>

    <section className="hero-heading" id="overview"><div><div className="eyebrow">INTERACTIVE PLAYGROUND <span>/</span> SIMULATION 001</div>
      <h1>Real estate sales. <em>Agent intelligence.</em></h1>
      <p>Explore a BlackPenguin-inspired property catalog while Nous Research Hermes qualifies a synthetic lead and proposes a showroom visit.</p></div>
      <div className="experiment-status"><span className="exp-icon"><Activity size={19}/></span><div><strong>Experiment live</strong><small>Lead journey · 45-min visit</small></div></div>
    </section>

    <main className="dashboard">
      <section className="inventory-column" id="inventory">
        <div className="column-heading"><div><span className="eyebrow">01 / KNOWLEDGE BASE</span><h2>Property inventory <span className="counter">{catalog?.properties.length ?? 0}</span></h2></div><span className="column-pill"><Layers3 size={13}/> Synthetic data</span></div>
        <div className="project-banner"><div className="project-super"><div className="building-icon"><House size={20}/></div><span>RESIDENTIAL COMMUNITY</span></div><h3>{project?.name || 'Residencial Jardines del Sol'}</h3>
          <p className="map-caption"><MapPin size={14}/> {project ? `${project.location.city}, ${project.location.country}` : 'Lima, Peru'}</p>
          <div className="hero-metrics"><div><strong>{catalog?.properties.length ?? 0}</strong><span>Available homes</span></div><div><strong>{project?.amenities.length ?? 0}</strong><span>Amenities</span></div><div><strong>{catalog?.sales_advisors.length ?? 0}</strong><span>Sales advisors</span></div></div>
          <button className="project-details" onClick={() => setShowProject(!showProject)}>Explore project details <ChevronRight size={16}/></button>
        </div>
        {showProject && project && <div className="project-expanded"><p>{project.value_proposition}</p><div className="eyebrow">PROJECT AMENITIES</div>
          <div className="amenities-list">{project.amenities.map((a) => <span key={a}><Check size={12}/>{a}</span>)}</div>
          <small>{project.location.street_address} · {project.developer}</small>
          <div className="project-inspection"><div><strong>Development</strong><span>{project.development_status || 'Not specified'}</span></div><div><strong>Project structure</strong><span>{project.phase_names?.join(', ') || 'Not specified'}</span></div><div><strong>Published revision</strong><span>v{project.published_revision ?? '—'} · synthetic</span></div><div><strong>Inventory</strong><span>{project.inventory_summary?.available_units ?? '—'} available / {project.inventory_summary?.reserved_units ?? '—'} reserved</span></div></div>
          <a href={project.general_brochure_pdf} target="_blank" rel="noopener noreferrer"><FileText size={14}/> View demo project brochure</a>
        </div>}
        <div className="inventory-toolbar"><div><SlidersHorizontal size={14}/> AVAILABLE UNITS <span>{properties.length} properties</span></div>
          <div className="filters">{(['All', 'Apartment', 'House'] as const).map((t) => <button key={t} className={filter === t ? 'selected' : ''} onClick={() => setFilter(t)}>{t === 'All' ? 'All' : t === 'Apartment' ? 'Apartments' : 'Houses'}</button>)}</div></div>
        {catalogError && <div className="error-alert">Could not load inventory: {catalogError}</div>}
        <div className="property-grid">{properties.map((u) => {
          const isMatch = state.shortlisted_unit_ids.includes(u.unit_id);
          return <button key={u.unit_id} className={`unit-card ${isMatch ? 'unit-matched' : ''}`} onClick={() => {setSelected(u);setPhoto(0);}}>
            <div className={`unit-image ${!u.media.gallery_urls.length ? 'image-unavailable' : ''}`}>
              {u.media.gallery_urls.length ? <img src={u.media.gallery_urls[0]} alt={`Synthetic concept for ${u.commercial_name}`}/> : <div className="placeholder"><Images size={26}/><span>No image gallery</span></div>}
              <span className={`unit-status ${u.status === 'Last Unit' ? 'status-last' : ''}`}>{u.status}</span>
              {isMatch && <span className="unit-match"><Sparkles size={12}/> Lead match</span>}
            </div>
            <div className="unit-content"><div className="unit-meta"><span>{u.property_type}</span><span>{u.unit_id}</span></div><h3>{u.commercial_name}</h3>
              <div className="unit-stats"><span><BedDouble size={14}/>{u.bedrooms} beds</span><span><Bath size={14}/>{u.bathrooms} baths</span><span><Square size={14}/>{u.area_sqm} m²</span></div>
              <div className="unit-bottom"><div><strong>{usd.format(u.price_usd)}</strong><small><Clock3 size={11}/>{displayReady(u.available_from_date)}</small></div><span className="open-unit"><ArrowUp size={16}/></span></div>
            </div>
          </button>;
        })}</div>
        <div className="team-panel"><div className="eyebrow"><Users size={15}/> HUMAN SALES ADVISORS</div><div className="team-list">{catalog?.sales_advisors.map((a, i) => <div key={a.advisor_id} className="advisor-item"><div className={`advisor-initial advisor-${i}`}>{a.name.split(' ').map((s) => s[0]).join('')}</div><div><strong>{a.name}</strong><small>{a.specialty}</small></div></div>)}</div></div>
        <p className="inventory-warning"><Info size={12}/> Fictional project, inventory, prices, advisors and media.</p>
      </section>

      <section className="chat-column" id="conversation"><div className="column-heading"><div><span className="eyebrow">02 / CONVERSATION</span><h2>Live sales chat <span className="heading-live"/></h2></div><span className="column-pill"><MessageCircle size={13}/> English</span></div>
        <div className="agent-line"><div className="agent-icon"><Bot size={22}/></div><div><strong>Ava <span>AI Sales Advisor</span></strong><small>Powered by Nous Research Hermes Agent</small></div><span className="agent-online"><span className="green-dot"/> Available</span></div>
        <div className="chat-body" ref={scrollRef}><div className="chat-begin"><span>YOUR INTERACTIVE LEAD SESSION</span><small>Each message invokes the Hermes conversation runtime.</small></div>
          {messages.map((m) => <div key={m.id} className={`chat-message ${m.role === 'user' ? 'from-lead' : 'from-agent'}`}>
            {m.role === 'assistant' && <div className="chat-avatar"><Bot size={16}/></div>}
            <div className="chat-bubble">{linkify(m.content)}</div></div>)}
          {busy && <div className="chat-message from-agent"><div className="chat-avatar"><Bot size={16}/></div><div className="chat-bubble typing"><i/><i/><i/></div></div>}
          {book && <div className="confirmation-card"><BadgeCheck size={19}/><div><strong>Simulated visit confirmed</strong><small>{book.date}, {book.start_time} · {book.advisor_name}</small></div></div>}
        </div>
        <div className="compose-area">{error && <div className="error-alert chat-error"><Info size={15}/>{error}<button aria-label="Dismiss" onClick={() => setError('')}><X size={14}/></button></div>}
          {state.turn_count === 0 && <div className="quick-prompts">{promptExamples.map((p, i) => <button key={p} onClick={() => setDraft(p)}><Sparkles size={11}/>{['Share my budget','Find a property','See photos'][i]}</button>)}</div>}
          <div className="composer"><textarea value={draft} disabled={busy || state.turn_count >= MAX_TURNS} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) {e.preventDefault();void send();}}} rows={2} placeholder={state.turn_count >= MAX_TURNS ? 'This demo session is complete.' : 'Ask about a property, budget, or visit...'}/><button aria-label="Send message" disabled={!draft.trim() || busy || state.turn_count >= MAX_TURNS} onClick={() => void send()}><ArrowUp size={19}/></button></div>
          <div className="composer-foot"><span><LockKeyhole size={11}/> Single-session demo · synthetic data only</span><span>{state.turn_count}/{MAX_TURNS} turns</span></div>
        </div>
      </section>

      <aside id="lead-state" className={`state-column ${profileFlash ? 'state-flash' : ''}`}><div className="column-heading"><div><span className="eyebrow">03 / LIVE STATE</span><h2>Lead intelligence</h2></div><span className="live-pill"><span className="green-dot"/> Live</span></div>
        <div className="tracking-summary"><div className="tracking-value">{filled}<span>/9</span></div><div><strong>Profile completeness</strong><small>Updated by agent tool calls</small><div className="progress-bar"><span style={{width: `${(filled / 9) * 100}%`}}/></div></div></div>
        <div className="state-body"><div className="state-section-title">CONVERSATION STAGE <Activity size={14}/></div>
          <div className="stage-summary"><span><Activity size={16}/></span><div><small>CURRENT STAGE</small><strong>{state.stage}</strong></div></div>
          <div className="state-section-title">LEAD PROFILE <span>{filled} of 9</span></div>
          <div className="profile-fields">{leadFields.map((f) => {
            const value = state.lead[f.key]; const hasValue = value !== null && value !== '';
            return <div key={f.key} className={`profile-field ${hasValue ? 'captured' : ''}`}><div className="field-check">{hasValue ? <Check size={12}/> : <i/>}</div><div><small>{f.label}</small><strong>{hasValue ? f.format ? f.format(value) : String(value) : f.fallback}</strong></div></div>;
          })}</div>
          <div className="state-section-title">BLACKPENGUIN · EVIDENCE <span>{Object.keys(state.bp_episode?.leads.lead_profile_data.sales_profile.current ?? {}).length}</span></div>
          {Object.entries(state.bp_episode?.leads.lead_profile_data.sales_profile.current ?? {}).length ? <div className="events">{Object.entries(state.bp_episode?.leads.lead_profile_data.sales_profile.current ?? {}).slice(-6).map(([key, fact]) => <div key={key}><i/><span><strong>{key}: {String(fact.value)}</strong><small>{fact.status} · {fact.source}</small><small title={fact.evidence}>Evidence: {fact.evidence.slice(0, 120)}</small><small className="evidence-ref">Message {fact.source_ref?.message_id?.slice(0, 8) || 'not referenced'} · {eventTime(fact.captured_at)}</small></span></div>)}</div> : <p className="muted-message">No declared commercial facts yet. Captured statements include provenance and evidence.</p>}
          <div className="state-section-title">MATCHED PROPERTIES <span>{state.shortlisted_unit_ids.length}</span></div>
          {state.shortlisted_unit_ids.length ? <div className="shortlist">{state.shortlisted_unit_ids.map((id) => { const u = catalog?.properties.find((x) => x.unit_id === id); return <button key={id} onClick={() => { if (u) {setSelected(u); setPhoto(0);} }}><House size={16}/><span><strong>{id}</strong><small>{u?.commercial_name}</small></span><ChevronRight size={14}/></button>; })}</div> : <p className="muted-message">Qualify the lead in chat to see matching homes here.</p>}
          <div className="state-section-title">ONSITE VISIT <CalendarDays size={14}/></div>
          {book ? <div className="booked-visit"><span><CheckCircle2 size={16}/> Simulated reservation</span><strong>{book.date} · {book.start_time}–{book.end_time}</strong><small>{book.advisor_name} · {book.unit_id || 'Project-wide visit'}</small><small>{book.booking_id}</small></div> : <div className="no-booking"><CalendarDays size={23}/><strong>No active appointment</strong><small>45-minute visit · 15-minute buffer</small></div>}
          {previousVisits.length ? <div className="visit-history"><small>VISIT HISTORY</small>{previousVisits.map(v => <div key={v.booking_id}><span>{v.date} · {v.start_time}</span><strong>{v.status || 'previous'}</strong></div>)}</div> : null}
          {state.booking_proposal?.options?.length && !book ? <div className="offer-panel"><div className="state-section-title offer-heading">OFFERED SLOTS <Clock3 size={14}/></div><small>Not held · Revalidated on confirmation</small>{state.booking_proposal.options.map((opt) => <div className="offer-row" key={`${opt.advisor_id}-${opt.date}-${opt.start_time}`}><CalendarDays size={14}/><span><strong>{opt.date} · {opt.start_time}–{opt.visit_end_time}</strong><small>{opt.advisor_name} · Buffer until {opt.buffer_end_time}</small></span></div>)}<small className="expires-note">Offer expires {eventTime(state.booking_proposal.expires_at)} · Demo only</small></div> : null}
          <div className="state-section-title">TOOL ACTIVITY <Terminal size={14}/></div>
          {state.events.length ? <div className="events">{[...state.events].reverse().slice(0, 7).map((e) => <div key={e.id}><i/><span><strong>{e.label}</strong><small>{e.event.replace('_',' ')} · {eventTime(e.timestamp)}</small></span></div>)}</div> : <p className="muted-message">Tool activity will appear here after new lead facts or property matches.</p>}
          <details className="raw-inspector"><summary><span className="material-symbols-outlined">data_object</span> Inspect structured state</summary><pre>{JSON.stringify({
            lead: state.lead,
            sales_profile_current: state.bp_episode?.leads.lead_profile_data.sales_profile.current ?? {},
            dialogue: state.bp_episode?.sales_dialogue_states?.state_json ?? {},
            booking_proposal: state.booking_proposal ?? null,
            meetings: state.bp_episode?.meetings ?? [],
          },null,2)}</pre></details>
        </div>
        <div className="state-foot"><ShieldCheck size={15}/> Signed state, synthetic data · no real appointments</div>
      </aside>
    </main>
    <footer className="page-footer"><span>HERMES AGENT LAB · Independent experiment using Nous Research Hermes</span><span>Next.js / FastAPI / OpenRouter / Vercel</span></footer>

    {selected && <div className="modal-overlay" onMouseDown={(e) => {if (e.target === e.currentTarget) setSelected(null);}}><div className="property-modal" ref={modalRef} role="dialog" aria-modal="true" aria-label={`Details for ${selected.commercial_name}`}>
      <div className="modal-heading"><span className="eyebrow">PROPERTY RECORD / {selected.unit_id}</span><button aria-label="Close dialog" onClick={() => setSelected(null)}><X size={19}/></button></div>
      <div className="modal-art">{selected.media.gallery_urls.length ? <img src={selected.media.gallery_urls[photo]} alt="Synthetic property concept illustration"/> : <div className="modal-no-gallery"><Images size={35}/> No gallery available</div>}
        {selected.media.gallery_urls.length > 1 && <div className="photo-controls"><button onClick={() => setPhoto((photo - 1 + selected.media.gallery_urls.length) % selected.media.gallery_urls.length)}><ArrowLeft size={15}/></button><span>{photo + 1}/{selected.media.gallery_urls.length}</span><button onClick={() => setPhoto((photo + 1) % selected.media.gallery_urls.length)}><ArrowRight size={15}/></button></div>}</div>
      <div className="modal-main"><div className="modal-kicker"><span>{selected.property_type} · {selected.status}</span><span>{selected.unit_id}</span></div><h2>{selected.commercial_name}</h2><p className="modal-price">{usd.format(selected.price_usd)}</p>
        <div className="modal-facts"><div><BedDouble size={17}/><strong>{selected.bedrooms}</strong><small>Bedrooms</small></div><div><Bath size={17}/><strong>{selected.bathrooms}</strong><small>Bathrooms</small></div><div><Square size={17}/><strong>{selected.area_sqm} m²</strong><small>Built area</small></div><div><Clock3 size={17}/><strong>{displayReady(selected.available_from_date)}</strong><small>Available</small></div></div>
        <div className="modal-description">{selected.property_type_description || 'No additional type description provided.'}</div>
        <div className="property-detail-grid">
          <div><small>TYPE CODE</small><strong>{selected.property_type_code || '—'}</strong></div>
          <div><small>FLOOR</small><strong>{selected.floor_number ?? 'Not specified'}</strong></div>
          <div><small>LOT AREA</small><strong>{selected.lot_area_m2 == null ? 'Not applicable' : `${selected.lot_area_m2} m²`}</strong></div>
          <div><small>CURRENCY</small><strong>{selected.currency ?? 'USD'}</strong></div>
          <div><small>INVENTORY VERIFIED</small><strong>{selected.demo_inventory_last_updated_at?.slice(0,10) ?? 'Unknown'} · demo</strong></div>
          <div><small>PUBLICATION</small><strong>Approved in synthetic catalog</strong></div>
        </div>
        <div className="modal-label">VERIFIED PROPERTY FEATURES</div>
        <div className="feature-inspector">{Object.entries(selected.confirmed_features || {}).length ? Object.entries(selected.confirmed_features || {}).map(([key,value]) => <div key={key}><span>{key.replaceAll('_',' ')}</span><strong>{typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value)}</strong></div>) : <small>No confirmed type-level features.</small>}</div>
        <div className="modal-label">HIGHLIGHTS</div><div className="modal-chips">{selected.highlights.map((x) => <span key={x}><CheckCircle2 size={13}/>{x}</span>)}</div>
        <div className="modal-label">MEDIA</div><div className="modal-links"><div><Images size={16}/><span>Gallery</span><strong>{selected.media.gallery_urls.length ? `${selected.media.gallery_urls.length} images` : 'Not available'}</strong></div><div><FileText size={16}/><span>Unit PDF</span>{selected.media.floorplan_brochure_pdf ? <a href={selected.media.floorplan_brochure_pdf} target="_blank" rel="noopener noreferrer">Open PDF <ArrowUp size={13}/></a> : <strong>Not available</strong>}</div></div>
        <button className="ask-about" onClick={() => {setDraft(`I would like to learn more about ${selected.unit_id}.`);setSelected(null);}}><MessageCircle size={17}/> Ask Ava about this home <ArrowRight size={16}/></button>
        <small className="modal-disclaimer">All project and home information is fictional. Illustrations and PDFs are synthetic demo assets.</small>
      </div></div></div>}
    </div>
  </div>;
}
