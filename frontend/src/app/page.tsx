'use client';

import {useCallback,useEffect,useRef,useState} from 'react';
import {ArrowDown,ArrowUp,Bath,BedDouble,Building2,CalendarDays,Check,ChevronDown,ChevronRight,FileText,Images,Info,LockKeyhole,LogOut,MapPin,MessageCircle,Plus,ShieldCheck,Square,Trash2,UserRound,Users,X} from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import SalesCalendar from './calendar';

type Unit={unit_id:string;blackpenguin_unit_id:string;property_type_id:string;property_type:'Apartment'|'House';commercial_name:string;property_type_code?:string;property_type_description?:string;bedrooms:number;bathrooms:number;area_sqm:number;lot_area_m2?:number|null;floor_number?:number|null;price_usd:number;currency?:string;available_from_date:string;status:string;confirmed_features?:Record<string,string|number|boolean>;highlights:string[];media:{gallery_urls:string[];gallery_url:string|null;floorplan_brochure_pdf:string|null;documents:{type:string;title:string;url:string;scope:'unit';unit_id:string}[]};demo_inventory_last_updated_at?:string;publication_status?:string;inventory_is_live?:boolean;phase_id?:string;phase_name?:string;property_model?:{model_name:string;code:string;bedrooms?:number;bathrooms?:number;built_area_min_m2?:number;built_area_max_m2?:number;lot_area_min_m2?:number;lot_area_max_m2?:number;total_units?:number;review_status?:string;inventory_updated_at?:string;verified_type_features?:{feature_key:string;verification_status:string;value_text?:string;value_number?:string;value_bool?:boolean}[]};};
type Catalog={project_info:{name:string;developer:string;project_type?:string;development_status?:string;short_description?:string;long_description?:string;project_code?:string;value_proposition:string;amenities:string[];phase_names?:string[];published_revision?:number;inventory_summary?:{total_units:number;available_units:number;reserved_units:number};general_brochure_pdf:string;location:{street_address:string;city:string;country:string;timezone?:string}};properties:Unit[]};
type Fact={id:string;field:string;value:unknown;status:string;source:string;evidence?:string;captured_at?:string;source_ref?:{message_id?:string}};
type Booking={meeting_id:string;advisor_name:string;unit_id:string|null;date:string;start_time:string;end_time:string;status:string};
type State={ui_fact_evidence?:Record<string,Evidence>;tool_observations?:ToolObservation[];lead:{first_name:string|null;last_name:string|null;phone:string|null;email:string|null;budget_usd:number|null;purchase_timeframe:string|null;property_type:string|null;min_bedrooms:number|null;interest_unit_id:string|null};stage:string;turn_count:number;shortlisted_unit_ids:string[];booked_visits:Booking[];property_interests?:{unit_id:string;status:string;source_message_id?:string}[];booking_proposal?:{proposal_id:string;status:string;expires_at:string;options:{advisor_id:string;advisor_name:string;date:string;start_time:string;visit_end_time:string;buffer_end_time:string}[]}|null;bp_episode?:{leads:{lead_profile_data:{sales_profile:{current:Record<string,Fact>}}}}};
type Evidence={source_message_id?:string;message_id?:string;captured_at?:string;evidence?:string;source?:string};
type ToolObservation={tool:string;status:string;fields:string[];timestamp:string;message_id?:string};
type ProfilePreview={lead:State['lead'];stage:string;property_interests:NonNullable<State['property_interests']>;facts:Record<string,Fact>;ui_fact_evidence:Record<string,Evidence>;tool_observations:ToolObservation[]};
type Message={id:string;role:'user'|'assistant';content:string;created_at:string;sequence_number?:number};
type Lead={id:string;conversation_id:string;first_name:string;last_name:string;email:string;phone:string;updated_at:string;created_at:string;revision:number;state:State;messages:Message[]};
type Summary={id:string;conversation_id:string;first_name:string;last_name:string;updated_at:string;last_message:string|null};
type Session={id:string;role:'administrator';owner_id:string;csrf_token:string};
const usd=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
const MAX_TURNS=14;
const EMPTY_FORM={first_name:'',last_name:'',phone:'',email:''};
function dateText(s:string){return s==='Immediate'?'Ready now':s||'Not specified';}
function formatChatTimestamp(value:string, timeZone:string){
  const dt=new Date(value);
  if(Number.isNaN(dt.getTime()))return 'Time unavailable';
  return new Intl.DateTimeFormat('en-GB',{timeZone,day:'2-digit',month:'short',year:'numeric',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(dt);
}
function chatDay(value:string,timeZone:string){
  const dt=new Date(value);
  return Number.isNaN(dt.getTime())?'Date unavailable':new Intl.DateTimeFormat('en-GB',
    {timeZone,day:'numeric',month:'long',year:'numeric'}).format(dt);
}
function FactRow({label,value,evidence,timeZone,onLocate}:{label:string;value:unknown;evidence?:Evidence;timeZone:string;onLocate:(id:string)=>void}){
  const text=value==null||value===''?'Not captured':String(value);
  const sourceId=evidence?.source_message_id||evidence?.message_id;
  return <div className="state-kv"><span>{label}</span><div className="fact-info"><strong>{text}</strong>
    {sourceId&&<button className="fact-provenance" title={evidence?.evidence||'Go to source message'}
      onClick={()=>onLocate(sourceId)} aria-label={`Find message supporting ${label}`}>
      {evidence?.captured_at?formatChatTimestamp(evidence.captured_at,timeZone):'View source'} · source</button>}
  </div></div>;
}

function Field({label,value}:{label:string;value:string|number|boolean|null|undefined}){const str=value==null?'Not specified':typeof value==='boolean'?(value?'Yes':'No'):String(value);return <div className="detail-field"><small>{label}</small><strong>{str}</strong></div>}
function safeLink(href:string|undefined){
  if(!href)return false;
  if(/^\/demo-assets\/[A-Za-z0-9_.-]+\.(svg|pdf)$/i.test(href))return true;
  if(/^\/properties\/[A-Za-z0-9-]+\/gallery\/?$/.test(href))return true;
  try{
    const u=new URL(href);
    const trustedHost=u.hostname==='testing-hermes-agent-alpha.vercel.app'||(typeof window!=='undefined'&&u.origin===window.location.origin);
    return (u.protocol==='https:'||(u.protocol==='http:'&&u.hostname==='localhost'&&typeof window!=='undefined'&&u.origin===window.location.origin))&&trustedHost&&(/^\/properties\/[A-Za-z0-9-]+\/gallery\/?$/.test(u.pathname)||/^\/demo-assets\/[A-Za-z0-9_.-]+\.pdf$/i.test(u.pathname))&&!u.search&&!u.hash;
  }catch{return false;}
}
function ChatMarkdown({text}:{text:string}){return <Markdown remarkPlugins={[remarkGfm]} components={{a:({href,children})=>safeLink(href)?<a href={href} target="_blank" rel="noopener noreferrer">{children}</a>:<span title="External or unapproved link hidden">{children}</span>}}>{text}</Markdown>}
function PropertyDetails({unit,project,onClose}:{unit:Unit;project:Catalog['project_info'];onClose:()=>void}){
  const [photo,setPhoto]=useState(0);
  const img=unit.media.gallery_urls;
  useEffect(()=>{const esc=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose()};window.addEventListener('keydown',esc);return()=>window.removeEventListener('keydown',esc)},[onClose]);
  return <div className="modal-shade" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><article className="property-detail-modal" role="dialog" aria-modal="true" aria-label={`${unit.unit_id} property details`}>
    <header className="modal-header"><div><small>APPROVED SYNTHETIC PROPERTY RECORD</small><h2>{unit.commercial_name} <span>{unit.unit_id}</span></h2></div><button aria-label="Close property details" onClick={onClose}><X size={19}/></button></header>
    {img.length?<div className="property-image-large"><img src={img[photo]} alt={`Illustrative ${unit.commercial_name}`}/>{img.length>1&&<div className="gallery-controls"><button onClick={()=>setPhoto((photo+img.length-1)%img.length)}>Previous</button><span>{photo+1}/{img.length}</span><button onClick={()=>setPhoto((photo+1)%img.length)}>Next</button></div>}</div>:<div className="no-image-large"><Images/> No gallery published</div>}
    <div className="modal-content"><div className="property-modal-top"><strong>{usd.format(unit.price_usd)}</strong><span>{unit.status} · {dateText(unit.available_from_date)}</span></div>
      <h3>Identity & inventory</h3><div className="detail-grid">
        <Field label="Project" value={project.name}/><Field label="Unit code" value={unit.unit_id}/><Field label="Property type" value={unit.property_type}/><Field label="Type code" value={unit.property_type_code}/>
        <Field label="Built area" value={`${unit.area_sqm} m²`}/><Field label="Lot area" value={unit.lot_area_m2==null?'Not specified':`${unit.lot_area_m2} m²`}/>{unit.property_type!=='House'&&<Field label="Floor" value={unit.floor_number}/>}<Field label="Bedrooms" value={unit.bedrooms}/><Field label="Bathrooms" value={unit.bathrooms}/><Field label="Currency" value={unit.currency||'USD'}/><Field label="Status" value={unit.status}/><Field label="Available from" value={dateText(unit.available_from_date)}/><Field label="Inventory updated (UTC)" value={unit.demo_inventory_last_updated_at}/><Field label="Published" value={unit.publication_status||'Demo approved'}/><Field label="Phase" value={unit.phase_name}/><Field label="Phase ID" value={unit.phase_id}/><Field label="Type ID" value={unit.property_type_id}/>
      </div>
      {unit.property_model&&<><h3>Property model (type-level facts)</h3><div className="detail-grid">
        <Field label="Model" value={unit.property_model.model_name}/>
        <Field label="Model code" value={unit.property_model.code}/>
        <Field label="Standard bedrooms" value={unit.property_model.bedrooms}/>
        <Field label="Standard bathrooms" value={unit.property_model.bathrooms}/>
        <Field label="Built area range" value={`${unit.property_model.built_area_min_m2 ?? '?'}–${unit.property_model.built_area_max_m2 ?? '?'} m²`}/>
        <Field label="Lot area range" value={`${unit.property_model.lot_area_min_m2 ?? '?'}–${unit.property_model.lot_area_max_m2 ?? '?'} m²`}/>
        <Field label="Total units of this model" value={unit.property_model.total_units}/>
        <Field label="Model review" value={unit.property_model.review_status}/>
        <Field label="Model inventory updated" value={unit.property_model.inventory_updated_at}/>
      </div></>}
      <h3>Property type description</h3><p>{unit.property_type_description||'No confirmed description available.'}</p>
      <h3>Verified property features</h3><div className="detail-grid">{Object.keys(unit.confirmed_features||{}).length?Object.entries(unit.confirmed_features||{}).map(([key,val])=><Field key={key} label={key.replaceAll('_',' ')} value={val}/>):<p>No verified features are published.</p>}</div>
      <h3>Project amenities</h3><div className="amenities-inline">{project.amenities.map(a=><span key={a}>{a}</span>)}</div>
      <h3>Gallery & property documents</h3><div className="asset-links">{unit.media.gallery_url?<a href={unit.media.gallery_url} target="_blank" rel="noopener noreferrer"><Images size={14}/> View gallery ({img.length})</a>:<span>No property gallery published</span>}{unit.media.documents.length?unit.media.documents.filter(d=>d.scope==='unit'&&d.unit_id===unit.blackpenguin_unit_id).map(doc=><a key={doc.url} href={doc.url} target="_blank" rel="noopener noreferrer"><FileText size={14}/> {doc.title}</a>):<span>No property PDF published</span>}</div>
      <h3>Project context</h3><div className="detail-grid"><Field label="Developer" value={project.developer}/><Field label="Development status" value={project.development_status}/><Field label="Project type" value={project.project_type}/><Field label="Location" value={`${project.location.street_address}, ${project.location.city}`}/></div>
      <details className="inspector"><summary>Inspect approved unit JSON</summary><pre>{JSON.stringify(unit,null,2)}</pre></details><small className="fineprint">The displayed facts are synthetic published information, not real estate offers. No internal sources are exposed.</small>
    </div>
  </article></div>;
}

export default function Page(){
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [auth,setAuth]=useState<Session|null>(null);
  const [credential,setCredential]=useState('');
  const [leads,setLeads]=useState<Summary[]>([]);
  const [active,setActive]=useState<Lead|null>(null);
  const [showLeads,setShowLeads]=useState(false),[showCreate,setShowCreate]=useState(false),[form,setForm]=useState({...EMPTY_FORM});
    const [selected,setSelected]=useState<Unit|null>(null);
  const [leftView,setLeftView]=useState<'inventory'|'calendar'>('inventory');
  const [draft,setDraft]=useState('');const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [calendarTick,setCalendarTick]=useState(0),[deleteId,setDeleteId]=useState<string|null>(null),[deleteConfirm,setDeleteConfirm]=useState('');
  const [showJump,setShowJump]=useState(false);
  const [liveProfile,setLiveProfile]=useState<ProfilePreview|null>(null);
  const [highlightedMessage,setHighlightedMessage]=useState<string|null>(null);
  const [timeZone,setTimeZone]=useState('America/Lima');
  const chatBody=useRef<HTMLDivElement>(null);
  const state=active?.state;
  const viewLead=liveProfile?.lead||state?.lead;
  const profileFacts=liveProfile?.facts||state?.bp_episode?.leads?.lead_profile_data?.sales_profile?.current||{};
  const profileEvidence=liveProfile?.ui_fact_evidence||state?.ui_fact_evidence||{};
  const interests=liveProfile?.property_interests||state?.property_interests||[];
  const observations=liveProfile?.tool_observations||state?.tool_observations||[];
  const additionalFacts=Object.values(profileFacts).filter(f=>!['budget','purchase_timeline','bedrooms','property_interest'].includes(f.field));
  const displayMessages=(active?.messages||[]).slice().sort((a,b)=>
    (a.sequence_number??Number.MAX_SAFE_INTEGER)-(b.sequence_number??Number.MAX_SAFE_INTEGER));
  function locateMessage(messageId:string){
    const el=Array.from(chatBody.current?.querySelectorAll<HTMLElement>('[data-message-id]')||[])
      .find(node=>node.dataset.messageId===messageId);
    if(el){el.scrollIntoView({behavior:'smooth',block:'center'});setHighlightedMessage(messageId);
      window.setTimeout(()=>setHighlightedMessage(v=>v===messageId?null:v),2400);}
  }
  const currentBooking=(state?.booked_visits||[]).find(b=>b.status==='confirmed');
  const properties=(catalog?.properties||[]).filter(p=>p.property_type==='House');
  const authorizedHeaders: Record<string,string>={'Content-Type':'application/json', ...(auth?{'X-Demo-Csrf':auth.csrf_token}:{})};
  const refreshLeads=useCallback(async()=>{const r=await fetch('/api/leads',{credentials:'same-origin'});if(r.ok)setLeads((await r.json()).leads)},[]);
  const selectLead=useCallback(async(id:string)=>{if(busy)return;setLiveProfile(null);setError('');const r=await fetch(`/api/leads/${id}`,{credentials:'same-origin'});if(!r.ok){setError('Unable to load lead');return;}setActive(await r.json());setDraft('');setShowLeads(false);setShowJump(false)},[busy]);
  useEffect(()=>{setTimeZone(Intl.DateTimeFormat().resolvedOptions().timeZone||'America/Lima')},[]);
  useEffect(()=>{fetch('/api/catalog').then(r=>r.json()).then(setCatalog).catch(()=>setError('Cannot load the published catalog.'));
    fetch('/api/me',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(s=>{if(s)setAuth(s)}).catch(()=>{});},[]);
  useEffect(()=>{if(auth)void refreshLeads();else{setLeads([]);setActive(null)}},[auth,refreshLeads]);
  // Refresh server-owned state on returning to the tab; keep the 20s poll as fallback.
  useEffect(()=>{
    if(!auth||busy)return;
    const sync=()=>{
      if(document.visibilityState!=='visible')return;
      void refreshLeads();
      if(active){fetch(`/api/leads/${active.id}`,{credentials:'same-origin'})
        .then(r=>r.ok?r.json():null).then(rec=>{if(rec&&rec.revision!==active.revision)setActive(rec)})
        .catch(()=>{});}
    };
    window.addEventListener('focus',sync);document.addEventListener('visibilitychange',sync);
    return()=>{window.removeEventListener('focus',sync);document.removeEventListener('visibilitychange',sync)};
  },[auth,active,busy,refreshLeads]);
  // A deleted lead disappears from other administrators' open tabs; stale UI never
  // remains an alternate history viewer after the server purges the record.
  useEffect(()=>{if(!auth)return;const id=setInterval(()=>{void refreshLeads();if(active&&!busy){fetch(`/api/leads/${active.id}`,{credentials:'same-origin'}).then(r=>{
    if(r.status===404){setActive(null);setShowLeads(false);return null;}
    return r.ok?r.json():null;
  }).then(rec=>{if(rec&&rec.revision!==active.revision)setActive(rec)}).catch(()=>{});}},20000);
    return()=>clearInterval(id);},[auth,active,busy,refreshLeads]);
  useEffect(()=>{if(chatBody.current&&!showJump)chatBody.current.scrollTo({top:chatBody.current.scrollHeight,behavior:'smooth'})},[active?.messages,showJump,busy]);
  async function login(){setBusy(true);setError('');try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Authentication failed');setAuth(data);setCredential('');}catch(e){setError(e instanceof Error?e.message:'Unable to log in')}finally{setBusy(false)}}
  async function logout(){if(!auth)return;try{await fetch('/api/auth/logout',{method:'POST',headers:authorizedHeaders})}finally{setAuth(null);setActive(null)}}
  async function newLead(){if(!auth||busy)return;setBusy(true);setError('');try{const r=await fetch('/api/leads',{method:'POST',headers:authorizedHeaders,body:JSON.stringify(form)});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Failed to create lead');setActive(data);setForm({...EMPTY_FORM});setShowCreate(false);setLeftView('inventory');await refreshLeads();}catch(e){setError(e instanceof Error?e.message:'Unable to create lead')}finally{setBusy(false)}}
  async function send(){
    const message=draft.trim();
    if(!auth||!active||!message||busy||state?.turn_count===MAX_TURNS)return;
    setBusy(true);setError('');setDraft('');setLiveProfile(null);
    const original=active;
    setActive({...active,messages:[...active.messages,{id:'pending',role:'user',content:message,
      created_at:new Date().toISOString()}]});
    let finished=false;
    try{
      const r=await fetch(`/api/leads/${original.id}/chat/stream`,{method:'POST',
        credentials:'same-origin',headers:authorizedHeaders,body:JSON.stringify({message})});
      if(!r.ok){const data=await r.json();throw new Error(data.detail||'Unable to contact Hermes');}
      if(!r.body)throw new Error('Streaming response unavailable');
      const reader=r.body.getReader();const decoder=new TextDecoder();let pending='';
      const handleEvent=(eventName:string,payload:Record<string,unknown>)=>{
        if(eventName==='received'){
          setActive(v=>v&&v.id===original.id?{...v,messages:v.messages.map(m=>m.id==='pending'?{
            ...m,id:String(payload.message_id),created_at:String(payload.created_at)}:m)}:v);
        }
        if(eventName==='profile_preview'&&payload.profile){setLiveProfile(payload.profile as ProfilePreview);}
        if(eventName==='committed'){
          const done=payload as unknown as {lead:Lead};setActive(done.lead);setLiveProfile(null);
          setCalendarTick(x=>x+1);void refreshLeads();setShowJump(false);finished=true;
        }
        if(eventName==='error')throw new Error(String(payload.detail||'Conversation failed. No changes saved.'));
      };
      const parseFrames=()=>{
        let boundary=pending.indexOf('\n\n');
        while(boundary!==-1){
          const frame=pending.slice(0,boundary);pending=pending.slice(boundary+2);
          let name='message';let data='';
          for(const line of frame.split('\n')){
            if(line.startsWith('event: '))name=line.slice(7);
            else if(line.startsWith('data: '))data+=line.slice(6);
          }
          if(data){handleEvent(name,JSON.parse(data) as Record<string,unknown>);}
          boundary=pending.indexOf('\n\n');
        }
      };
      while(true){const {value,done}=await reader.read();if(done)break;
        pending=(pending+decoder.decode(value,{stream:true})).replace(/\r\n/g,'\n');parseFrames();}
      pending+=decoder.decode().replace(/\r\n/g,'\n');parseFrames();
      if(!finished)throw new Error('Hermes stream ended without saving the turn.');
    }catch(e){setError(e instanceof Error?e.message:'Conversation failed');
      setActive(original);setLiveProfile(null);setDraft(message);
    }finally{setBusy(false);}
  }
  async function permanentDelete(){if(!auth||!deleteId)return;const record=leads.find(l=>l.id===deleteId);if(!record||deleteConfirm!==`${record.first_name} ${record.last_name}`)return;setBusy(true);setError('');try{const r=await fetch(`/api/admin/leads/${deleteId}`,{method:'DELETE',headers:authorizedHeaders});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Unable to delete lead');if(active?.id===deleteId)setActive(null);setDeleteId(null);setDeleteConfirm('');await refreshLeads();setCalendarTick(x=>x+1);}catch(e){setError(e instanceof Error?e.message:'Delete failed')}finally{setBusy(false)}}
  const leadToDelete=leads.find(l=>l.id===deleteId);
  return <div className="app-shell">
    <aside className="sidebar"><div className="bp-logo"><span>black</span><b>penguin</b><small>.ai</small></div><nav><a href="#conversation" className="selected-nav"><MessageCircle size={17}/> Hermes agent</a></nav><div className="side-foot"><ShieldCheck size={15}/> Synthetic evaluation demo</div></aside>
    <div className="shell-content"><header className="topbar"><div className="workspace-name"><strong>Hermes Agent Lab</strong><span>Real estate evaluation workspace</span></div><div className="topbar-actions"><small>OpenRouter · gpt-4o-mini</small>{auth?<><span className="role-tag">{auth.role}</span><button className="ghost" onClick={()=>void logout()}><LogOut size={14}/> Sign out</button></>:<span className="role-tag">Sign in required</span>}</div></header>
      {error&&<div className="global-error" role="alert"><Info size={14}/>{error}<button onClick={()=>setError('')}><X size={13}/></button></div>}
      {!auth?<div className="login-shell"><div className="login-panel"><LockKeyhole size={24}/><h1>Access the Hermes evaluation workspace</h1><p>Sign in to manage fictional leads, their conversations and appointment history.</p><label>Administrator password<input type="password" value={credential} onChange={e=>setCredential(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void login()}}/></label><button className="primary" disabled={!credential||busy} onClick={()=>void login()}>Open workspace</button><small>Administrator-only demo · use fictional contact information.</small></div></div>:
      <main className="workspace" id="conversation">
        <section className="panel inventory-panel"><div className="panel-top"><h2>{leftView==='inventory'?'Property inventory':'Sales calendar'}</h2><div className="toggle-row"><button className={leftView==='inventory'?'current':''} onClick={()=>setLeftView('inventory')}>Inventory</button><button className={leftView==='calendar'?'current':''} onClick={()=>setLeftView('calendar')}>Calendar</button></div></div>
          <div className="panel-scroll">
            {leftView==='inventory'?<>
              {catalog&&<section className="project-strip"><div className="project-subtitle"><Building2 size={17}/> Project overview</div><h3>{catalog.project_info.name}</h3><div className="muted"><MapPin size={13}/>{catalog.project_info.location.city}, {catalog.project_info.location.country}</div><p>{catalog.project_info.value_proposition}</p><details><summary>Full project information</summary><div className="detail-grid"><Field label="Developer" value={catalog.project_info.developer}/><Field label="Development status" value={catalog.project_info.development_status}/><Field label="Type" value={catalog.project_info.project_type}/><Field label="Project code" value={catalog.project_info.project_code}/><Field label="Address" value={catalog.project_info.location.street_address}/><Field label="Published revision" value={catalog.project_info.published_revision}/></div><div className="amenities-inline">{catalog.project_info.amenities.map(a=><span key={a}>{a}</span>)}</div>{catalog.project_info.general_brochure_pdf&&<a href={catalog.project_info.general_brochure_pdf} target="_blank" rel="noopener noreferrer"><FileText size={14}/> Project brochure</a>}</details></section>}
              <div className="filter-row"><strong>{properties.length} houses for sale</strong></div>
              <div className="unit-list">{properties.map(u=><button className="unit-row" key={u.unit_id} onClick={()=>setSelected(u)}><div className="unit-thumb">{u.media.gallery_urls.length?<img src={u.media.gallery_urls[0]} alt={`Illustration of ${u.unit_id}`}/>:<Images size={20}/>}</div><div><div className="unit-name"><strong>{u.unit_id}</strong><span>{u.property_type}</span></div><p>{u.commercial_name}</p><div className="unit-metrics"><BedDouble size={12}/>{u.bedrooms} <Bath size={12}/>{u.bathrooms} <Square size={12}/>{u.area_sqm} m²</div><div className="unit-price">{usd.format(u.price_usd)}<small>{u.status}</small></div></div><ChevronRight size={16}/></button>)}</div>
              <p className="fineprint">House prices and availability are synthetic. Full published facts are available in each house record; reserved homes are never recommended.</p>
            </>:<SalesCalendar refresh={calendarTick}/>}
          </div>
        </section>
        <section className="panel chat-panel"><div className="panel-top chat-top"><div><h2>Hermes conversation</h2><small>{active?`${active.first_name} ${active.last_name} · ${active.state.turn_count}/${MAX_TURNS} turns`:'No lead selected'}</small></div><button className="primary compact" onClick={()=>setShowCreate(true)}><Plus size={14}/> New lead</button></div>
          <div className="lead-switch"><button className="lead-switch-button" onClick={()=>setShowLeads(!showLeads)}><Users size={16}/><span>{active?`${active.first_name} ${active.last_name}`:'Select a lead'}</span><ChevronDown size={15}/></button>{showLeads&&<div className="lead-dropdown">{leads.length?leads.map(l=><div className={`lead-option ${active?.id===l.id?'active':''}`} key={l.id}><button onClick={()=>void selectLead(l.id)}><UserRound size={15}/><span><strong>{l.first_name} {l.last_name}</strong><small>{l.last_message?.slice(0,55)||'No messages'}</small></span></button>{auth.role==='administrator'&&<button aria-label={`Delete ${l.first_name} ${l.last_name}`} className="icon-btn danger" onClick={()=>{setDeleteId(l.id);setShowLeads(false)}}><Trash2 size={15}/></button>}</div>):<p>No leads yet. Create one to begin.</p>}</div>}</div>
          {active?<><div className="chat-messages" ref={chatBody} onScroll={e=>{const el=e.currentTarget;setShowJump(el.scrollHeight-el.scrollTop-el.clientHeight>110)}}>
            {displayMessages.map((m,i)=>{
              const day=chatDay(m.created_at,timeZone);
              const previous=i>0?chatDay(displayMessages[i-1].created_at,timeZone):null;
              return <div key={m.id}>
                {day!==previous&&<div className="chat-date-divider" aria-label={`Messages from ${day}`}>{day}</div>}
                <div data-message-id={m.id} className={`message ${m.role} ${busy&&!m.sequence_number?'message-provisional':''} ${highlightedMessage===m.id?'message-highlighted':''}`}>
                  <div className="message-meta"><span>{m.role==='assistant'?'AI Sales Agent · Hermes':'Lead'}</span>
                    <time dateTime={m.created_at} title={m.created_at}>{formatChatTimestamp(m.created_at,timeZone)}{busy&&!m.sequence_number?' · pending':''}</time></div>
                  <div className="message-content"><ChatMarkdown text={m.content}/></div>
                </div>
              </div>;
            })}
            {busy&&<div className="message assistant"><div className="message-role">AI Sales Agent · Hermes</div><div className="message-content">Thinking…</div></div>}
          </div>{showJump&&<button className="jump-bottom" onClick={()=>{chatBody.current?.scrollTo({top:chatBody.current.scrollHeight,behavior:'smooth'});setShowJump(false)}}><ArrowDown size={14}/> Latest</button>}
          <div className="composer-area"><div className="composer-box"><textarea rows={2} value={draft} onChange={e=>setDraft(e.target.value)} disabled={busy||state?.turn_count===MAX_TURNS} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();void send()}}} placeholder={state?.turn_count===MAX_TURNS?'Demo limit reached':'Type as this fictional lead…'}/><button className="send-btn" disabled={!draft.trim()||busy||state?.turn_count===MAX_TURNS} aria-label="Send" onClick={()=>void send()}><ArrowUp size={18}/></button></div><div className="fineprint">Conversations and meetings are saved in the shared demo database. No real sales notifications are sent.</div></div></>:<div className="empty-chat"><MessageCircle size={28}/><h3>Start with a demo lead</h3><p>Create fictional contact details or select a previous conversation.</p><button className="primary" onClick={()=>setShowCreate(true)}><Plus size={14}/> New lead</button></div>}
        </section>
        <section className="panel lead-panel"><div className="panel-top"><div><h2>Lead state</h2><small>{liveProfile?'Updating · pending save':active?'Saved facts · '+timeZone:'Waiting for a lead'}</small></div></div>
          <div className="panel-scroll lead-scroll">{active&&state?<>{liveProfile&&<p className="profile-preview-note">Provisional facts · awaiting Hermes completion and Turso commit</p>}
            <section className="state-section"><h3>Contact</h3><FactRow label="Name" value={`${viewLead?.first_name||active.first_name} ${viewLead?.last_name||active.last_name}`} evidence={profileEvidence.first_name||profileEvidence.last_name} timeZone={timeZone} onLocate={locateMessage}/><FactRow label="Phone" value={viewLead?.phone||active.phone} evidence={profileEvidence.phone} timeZone={timeZone} onLocate={locateMessage}/><FactRow label="Email" value={viewLead?.email||active.email} evidence={profileEvidence.email} timeZone={timeZone} onLocate={locateMessage}/></section>
            <section className="state-section"><h3>Qualification</h3>
              <FactRow label="Budget" value={viewLead?.budget_usd?usd.format(viewLead.budget_usd):profileFacts.budget?.value} evidence={profileFacts.budget?{...profileFacts.budget,source_message_id:profileFacts.budget.source_ref?.message_id}:undefined} timeZone={timeZone} onLocate={locateMessage}/>
              <FactRow label="Purchase timeframe" value={viewLead?.purchase_timeframe||profileFacts.purchase_timeline?.value} evidence={profileFacts.purchase_timeline?{...profileFacts.purchase_timeline,source_message_id:profileFacts.purchase_timeline.source_ref?.message_id}:undefined} timeZone={timeZone} onLocate={locateMessage}/>
              <FactRow label="Property type" value={viewLead?.property_type} evidence={profileEvidence.property_type} timeZone={timeZone} onLocate={locateMessage}/>
              <FactRow label="Bedrooms" value={viewLead?.min_bedrooms} evidence={profileFacts.bedrooms?{...profileFacts.bedrooms,source_message_id:profileFacts.bedrooms.source_ref?.message_id}:undefined} timeZone={timeZone} onLocate={locateMessage}/>
              <small className="stage-label">{liveProfile?.stage||state.stage}</small>
            </section>
            <section className="state-section"><h3>Property interest</h3><div className="state-kv"><span>Active unit</span><strong>{viewLead?.interest_unit_id||'Not declared'}</strong></div>{interests.length?<div className="interest-list">{interests.map(i=><button key={i.unit_id} onClick={()=>{const u=catalog?.properties.find(u=>u.unit_id===i.unit_id);if(u)setSelected(u)}}><span>{i.unit_id}</span><small>{i.status}</small><ChevronRight size={13}/></button>)}</div>:<small className="muted">Unit mentions and explicit interest will appear here.</small>}{state.shortlisted_unit_ids.length>0&&<small className="muted">Matched by agent: {state.shortlisted_unit_ids.join(', ')}</small>}</section>
            {additionalFacts.length>0&&<section className="state-section"><h3>Other captured commercial facts</h3>
              {additionalFacts.map(f=><FactRow key={f.id} label={f.field.replaceAll('_',' ')} value={f.value}
                evidence={{...f,source_message_id:f.source_ref?.message_id}} timeZone={timeZone} onLocate={locateMessage}/>)}
            </section>}
            {observations.length>0&&<details className="inspector"><summary>Capture activity · Hermes tools and validation ({observations.length})</summary>
              {observations.slice(-8).map((o,i)=><div className="evidence-line" key={`${o.timestamp}-${i}`}>
                <strong>{o.tool} · {o.status}</strong><small>{o.fields.join(', ')} · {formatChatTimestamp(o.timestamp,timeZone)}</small>
              </div>)}
            </details>}
            <section className="state-section"><h3>Appointment</h3>{currentBooking?<div className="appointment-card"><div className="status-line"><Check size={13}/> Confirmed · Demo only</div><strong>{currentBooking.date} · {currentBooking.start_time}–{currentBooking.end_time}</strong><small>{currentBooking.advisor_name}</small><small>{currentBooking.unit_id||'General project visit'}</small></div>:<div className="no-appointment">No confirmed appointment</div>}{state.booking_proposal?.status==='offered'&&<div className="offer-summary"><small>Offered · not booked · expires {new Date(state.booking_proposal.expires_at).toLocaleTimeString()}</small>{state.booking_proposal.options.map(opt=><div key={`${opt.advisor_id}-${opt.date}-${opt.start_time}`}>{opt.date} {opt.start_time} · {opt.advisor_name}</div>)}</div>}{state.booked_visits.filter(b=>b.status!=='confirmed').length>0&&<details className="inspector"><summary>Previous visits</summary>{state.booked_visits.filter(b=>b.status!=='confirmed').map(b=><div className="state-kv" key={b.meeting_id}><span>{b.date}</span><strong>{b.status}</strong></div>)}</details>}</section>
            <details className="inspector"><summary>Evidence & provenance</summary>{Object.values(profileFacts).map(f=><div className="evidence-line" key={f.id}><strong>{f.field}: {String(f.value)}</strong><small>{f.status} · {f.source}</small><p>{f.evidence}</p></div>)}</details>
            <details className="inspector"><summary>Inspect conversation state JSON</summary><pre>{JSON.stringify(state,null,2)}</pre></details>
          </>:<div className="lead-empty"><UserRound size={25}/><p>Select a lead to inspect its qualification, interest and appointments.</p></div>}</div>
        </section>
      </main>}
    </div>
    {selected&&catalog&&<PropertyDetails unit={selected} project={catalog.project_info} onClose={()=>setSelected(null)}/>}
    {showCreate&&<div className="modal-shade" onMouseDown={e=>{if(e.target===e.currentTarget)setShowCreate(false)}}><form className="small-modal" onSubmit={e=>{e.preventDefault();void newLead()}}><div className="modal-header"><h2>New demo lead</h2><button type="button" onClick={()=>setShowCreate(false)}><X size={18}/></button></div><p>Enter fictional contact information to start a separate Hermes conversation.</p><div className="form-grid">{(['first_name','last_name','phone','email'] as const).map(key=><label key={key}>{({first_name:'First name',last_name:'Last name',phone:'Phone number',email:'Email'})[key]}<input required autoComplete="off" type={key==='email'?'email':'text'} value={form[key]} onChange={e=>setForm(v=>({...v,[key]:e.target.value}))} placeholder={key==='email'?'alex@example.test':key==='phone'?'+51999999999':undefined}/></label>)}</div><button className="primary" type="submit" disabled={busy}>Create lead & start conversation</button><small className="fineprint">Registration details are stored as structured input, not inferred by Hermes.</small></form></div>}
    {deleteId&&leadToDelete&&<div className="modal-shade" onMouseDown={e=>{if(e.target===e.currentTarget)setDeleteId(null)}}><div className="small-modal"><div className="modal-header"><h2>Delete lead permanently</h2><button onClick={()=>setDeleteId(null)}><X size={18}/></button></div><p>This permanently removes the contact, conversation, profile, facts, appointment offers, meetings and their occupied slots from the demo database.</p><label>Type <strong>{leadToDelete.first_name} {leadToDelete.last_name}</strong> to confirm<input value={deleteConfirm} onChange={e=>setDeleteConfirm(e.target.value)}/></label><button className="delete-action" disabled={busy||deleteConfirm!==`${leadToDelete.first_name} ${leadToDelete.last_name}`} onClick={()=>void permanentDelete()}><Trash2 size={15}/> Permanently delete lead</button><small className="fineprint">Only administrators can perform this action. Provider backups and external AI logs are outside application-level deletion.</small></div></div>}
  </div>;
}
