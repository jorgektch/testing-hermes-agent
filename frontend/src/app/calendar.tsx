'use client';

import {useEffect, useMemo, useState} from 'react';
import {CalendarDays, ChevronLeft, ChevronRight, Expand, Minimize, RefreshCw, X} from 'lucide-react';

export type CalendarAdvisor={advisor_id:string;user_id:string;name:string;specialty:string};
export type CalendarSlot={advisor_id:string;sales_user_id:string;date:string;start_time:string;starts_at:string;visit_end_time:string;buffer_end_time:string;timezone:string};
export type CalendarMeeting={id:string;lead_id:string|null;advisor_id:string;unit_id:string|null;starts_at:string;ends_at:string;occupied_until:string;status:string;first_name:string|null;last_name:string|null;source:string};
type CalendarResponse={advisors:CalendarAdvisor[];slots:CalendarSlot[];meetings:CalendarMeeting[];project_timezone:string;server_time_utc:string};
const PROJECT_TZ='America/Lima';
// Deterministic server render; detect the browser timezone after hydration.
const INITIAL_TZ=PROJECT_TZ;
const pad=(n:number)=>String(n).padStart(2,'0');
function dateParts(d:Date,tz:string){const parts=new Intl.DateTimeFormat('en-US',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d);const o:Record<string,string>={};parts.forEach(p=>o[p.type]=p.value);return {date:`${o.year}-${o.month}-${o.day}`,time:`${o.hour}:${o.minute}`,hour:Number(o.hour),minute:Number(o.minute)};}
function addDay(date:string,days:number){const d=new Date(date+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);}
function localTime(iso:string,tz:string){return dateParts(new Date(iso),tz);}
function readable(date:string,tz:string){return new Intl.DateTimeFormat('en-US',{weekday:'short',month:'short',day:'numeric',timeZone:tz}).format(new Date(date+'T12:00:00Z'));}
function minute(v:string){const [h,m]=v.split(':').map(Number);return h*60+m;}
const START=8*60,END=19*60,STEP=60,HEIGHT=60;
const timeLabels=Array.from({length:(END-START)/STEP},(_,i)=>`${pad(Math.floor((START+i*STEP)/60))}:${pad((START+i*STEP)%60)}`);

export default function SalesCalendar({refresh=0}:{refresh?:number}){
  const [viewerZoneName,setViewerZoneName]=useState(INITIAL_TZ);
  const [viewerZone,setViewerZone]=useState(true),[mode,setMode]=useState<'day'|'week'>('day');
  const [now,setNow]=useState<Date>(()=>new Date());
  const tz=viewerZone?viewerZoneName:PROJECT_TZ;
  const [day,setDay]=useState(()=>dateParts(new Date(),INITIAL_TZ).date);
  const [selected,setSelected]=useState<string[]>([]);
  const [calendar,setCalendar]=useState<CalendarResponse|null>(null);
  const [error,setError]=useState('');
  const [expanded,setExpanded]=useState(false);
  const [loading,setLoading]=useState(false);
  const chosen=useMemo(()=>new Set(selected.length?selected:(calendar?.advisors||[]).map(a=>a.advisor_id)),[selected,calendar]);
  const visible=(calendar?.advisors||[]).filter(a=>chosen.has(a.advisor_id));
  useEffect(()=>{const viewer=Intl.DateTimeFormat().resolvedOptions().timeZone||PROJECT_TZ;setViewerZoneName(viewer);setDay(dateParts(new Date(),viewer).date);const id=setInterval(()=>setNow(new Date()),30000);return ()=>clearInterval(id);},[]);
  useEffect(()=>{
    let active=true;
    const load=async()=>{setLoading(true);setError('');try{
      // Fetch a wide enough range to convert project UTC events to viewer-local dates.
      const start=addDay(day,-1),days=mode==='week'?9:3;
      const res=await fetch(`/api/calendar?start=${start}&days=${days}`,{credentials:'same-origin'});
      if(!res.ok)throw new Error((await res.json()).detail||`Calendar HTTP ${res.status}`);
      if(active)setCalendar(await res.json());
    }catch(e){if(active)setError(e instanceof Error?e.message:'Calendar unavailable');}
    finally{if(active)setLoading(false);}};
    void load();const interval=setInterval(()=>void load(),20000);
    const onFocus=()=>void load();window.addEventListener('focus',onFocus);
    return()=>{active=false;clearInterval(interval);window.removeEventListener('focus',onFocus);};
  },[day,mode,refresh]);
  const bookings=calendar?.meetings||[],slots=calendar?.slots||[];
  const byDay=(d:string,a:string)=>({
    slots:slots.filter(s=>s.sales_user_id===a&&localTime(s.starts_at,tz).date===d),
    meetings:bookings.filter(m=>m.advisor_id===a&&localTime(m.starts_at,tz).date===d),
  });
  const shownDays=mode==='day'?[day]:Array.from({length:7},(_,i)=>addDay(day,i));
  const today=dateParts(now,tz).date;
  const current=dateParts(now,tz);
  function shift(n:number){setDay(d=>addDay(d,n*(mode==='day'?1:7)));}
  function selectAll(){setSelected([]);}
  function toggle(id:string){const all=calendar?.advisors.map(a=>a.advisor_id)||[];const current=new Set(selected.length?selected:all);if(current.has(id))current.delete(id);else current.add(id);setSelected([...current]);}
  return <section className={`sales-calendar ${expanded?'calendar-expanded':''}`} aria-label="Sales advisor calendar">
    <div className="calendar-header">
      <div><h3><CalendarDays size={16}/> Sales calendar</h3><small>Hourly starts · 45-minute visits + 15-minute buffer</small></div>
      <button className="icon-btn" title={expanded?'Collapse calendar':'Expand calendar'} aria-label="Toggle expanded calendar" onClick={()=>setExpanded(!expanded)}>{expanded?<Minimize size={17}/>:<Expand size={17}/>}</button>
    </div>
    <div className="calendar-controls">
      <div className="calendar-nav"><button aria-label="Previous date" onClick={()=>shift(-1)}><ChevronLeft size={15}/></button><button onClick={()=>setDay(dateParts(new Date(),tz).date)}>Today</button><button aria-label="Next date" onClick={()=>shift(1)}><ChevronRight size={15}/></button><strong>{readable(day,tz)}</strong></div>
      <div className="calendar-modes"><button className={mode==='day'?'active':''} onClick={()=>setMode('day')}>Day</button><button className={mode==='week'?'active':''} onClick={()=>setMode('week')}>Week</button></div>
    </div>
    <div className="calendar-filters">
      <label className="check-line"><input type="checkbox" checked={selected.length===0||selected.length===calendar?.advisors.length} onChange={selectAll}/> All</label>
      {calendar?.advisors.map(a=><label className="check-line" key={a.advisor_id}><input type="checkbox" checked={chosen.has(a.advisor_id)} onChange={()=>toggle(a.advisor_id)}/>{a.name.split(' ')[0]}</label>)}
      <button className="timezone-toggle" onClick={()=>{setViewerZone(!viewerZone);setDay(dateParts(new Date(),viewerZone?PROJECT_TZ:viewerZoneName).date);}}>{viewerZone?'Viewer':'Project'} time <span>· {tz}</span></button>
    </div>
    <div className="calendar-clock"><span className="live-dot"/> Now {current.time} · {readable(today,tz)} <span className="muted">(device zone: {viewerZoneName})</span>{loading&&<RefreshCw size={12}/>}</div>
    {error&&<div className="error-text">{error}</div>}
    {mode==='day'?<div className="calendar-scroll">
      <div className="calendar-day" style={{minWidth:`${68+visible.length*155}px`,gridTemplateColumns:`58px repeat(${visible.length},minmax(155px,1fr))`}}>
        <div className="calendar-time-head">Time</div>
        {visible.map(a=><div key={a.user_id} className="calendar-advisor-head" title={a.specialty}>{a.name}</div>)}
        <div className="calendar-time-track">{timeLabels.map(t=><div className="calendar-time-row" key={t}>{t}</div>)}</div>
        {visible.map(a=>{
          const data=byDay(day,a.user_id);
          return <div className="calendar-advisor-lane" key={a.user_id} style={{height:timeLabels.length*HEIGHT}}>
            {timeLabels.map(t=><div className="calendar-cell" key={t} style={{top:((minute(t)-START)/STEP)*HEIGHT,height:HEIGHT}}/>)}
            {data.slots.map(s=>{const local=localTime(s.starts_at,tz),m=minute(local.time);if(m<START||m>=END)return null;
              return <div className="calendar-slot" key={`${s.advisor_id}-${s.starts_at}`} style={{top:(m-START)/STEP*HEIGHT+2,height:HEIGHT-4}} title={`${s.start_time} · Available (not held)`}>{local.time}<small>Available</small></div>;})}
            {data.meetings.map(m=>{const local=localTime(m.starts_at,tz),mt=minute(local.time);const visitMinutes=(new Date(m.ends_at).getTime()-new Date(m.starts_at).getTime())/60000;const bufferMinutes=(new Date(m.occupied_until).getTime()-new Date(m.ends_at).getTime())/60000;if(mt<START||mt>=END)return null;
              return <div className="calendar-event" key={m.id} style={{top:(mt-START)/STEP*HEIGHT,height:(visitMinutes+bufferMinutes)/STEP*HEIGHT}}
                title={`${m.status} · ${local.time} · ${m.unit_id||'Project visit'} · ${m.first_name?`${m.first_name} ${m.last_name||''}`:'Busy'}`}>
                <div className="calendar-event-visit" style={{height:`${visitMinutes/(visitMinutes+bufferMinutes)*100}%`}}><strong>{local.time}</strong><small>{m.first_name?`${m.first_name} · `:''}{m.unit_id||'Booked'}</small></div>
                {bufferMinutes>0&&<div className="calendar-event-buffer" title={`${bufferMinutes} minute buffer`}>Buffer</div>}
              </div>;})}
            {day===today&&minute(current.time)>=START&&minute(current.time)<END&&<div className="calendar-now-line" style={{top:(minute(current.time)-START)/STEP*HEIGHT}}/>}
          </div>;
        })}
        {!visible.length&&<div className="calendar-empty">Select advisors to display their schedules.</div>}
      </div>
    </div>:<div className="calendar-scroll"><div className="calendar-week">
      {shownDays.map(d=><div className={`calendar-week-day ${d===today?'calendar-today':''}`} key={d}>
        <div className="calendar-week-head">{readable(d,tz)}</div>
        {visible.map(a=>{const data=byDay(d,a.user_id);return <div key={a.user_id} className="calendar-week-advisor"><strong>{a.name.split(' ')[0]}</strong><small>{data.slots.length} slots</small>{data.meetings.map(m=><span className="week-booking" key={m.id}>{localTime(m.starts_at,tz).time} Booked · {m.unit_id||'Visit'}</span>)}{data.slots.slice(0,3).map(s=><span className="week-slot" key={s.starts_at}>{localTime(s.starts_at,tz).time} available</span>)}{data.slots.length>3&&<small>+{data.slots.length-3} more slots</small>}</div>;})}
      </div>)}
    </div></div>}
    <div className="calendar-legend"><span><i className="legend-available"/>Available</span><span><i className="legend-booked"/>Booked</span><span><i className="legend-buffer"/>Buffer</span><span><i className="legend-now"/>Now</span></div>
    <p className="calendar-notice">Synthetic schedules · Shared demo bookings · Google Calendar not connected. Book exclusively through Hermes chat.</p>
  </section>;
}
