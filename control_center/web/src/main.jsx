import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, Database, FileText, History, Play, RefreshCw, Search, ShieldCheck, ChevronRight, X, ExternalLink, Mail, Check, Minus, AlertTriangle, Clock, Settings2, Save, ArrowRight, FlaskConical} from 'lucide-react';
import './style.css';
import {decisionMatchesFilter, resultMessage, runSectionExpanded, selectedRunWeek, sourceStatus, stageDuration} from './workflow-state.js';

async function api(path, body) {
  const response = await fetch('/api' + path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Request failed');
  return result;
}
const Icon = ({icon: I, ...props}) => <I size={17} strokeWidth={1.7} {...props}/>;
const sec = n => n == null ? '--' : n.toFixed(1) + 's';
const stamp = s => s ? new Date(s).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '';
const label = s => ({local_completed:'Local only',completed:'Completed',failed:'Failed',running:'Running',interrupted:'Interrupted',raw:'Not prepared',prepared:'Prepared'})[s] || s;
const stages = ['review','verification','validation','publication','confirmation'];
function stepState(run, metrics, stage, i){
  const reviewing=run?.kind==='review',active=run?.status==='running';
  let index=reviewing?stages.indexOf(run?.stage):-1;
  if(reviewing&&run?.stage==='complete')index=run.status==='local_completed'?3:5;
  const complete=stage==='review'?metrics.review_complete:stage==='verification'?metrics.verification_complete:null;
  const done=reviewing&&(complete??index>i);
  const running=active&&reviewing&&!done&&(stage==='verification'?(metrics.verification_started??index===i):index===i);
  return {done,running,skipped:run?.status==='local_completed'&&i>=3};
}
function Badge({value}) {return <span className={'badge ' + (value === 'Yes' || value === 'completed' || value === 'prepared' ? 'green' : value === 'Maybe' || value === 'running' ? 'amber' : value === 'No' ? 'neutral' : value === 'failed' ? 'red' : 'neutral')}>{label(value)}</span>}

function App(){
  const [overview,setOverview]=useState(null),[view,setView]=useState('run'),[week,setWeek]=useState(''),[run,setRun]=useState(null),[runs,setRuns]=useState([]),[deals,setDeals]=useState([]),[search,setSearch]=useState(''),[fit,setFit]=useState('All'),[detail,setDetail]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[health,setHealth]=useState(null),[evaluation,setEvaluation]=useState(null),[target,setTarget]=useState('test'),[now,setNow]=useState(Date.now()),[clicked,setClicked]=useState(null),[browserSeconds,setBrowserSeconds]=useState(null);
  const selectedWeek=overview?.weeks.find(w=>w.id===week);
  async function load(){
    const data=await api('/overview');setOverview(data);
    setWeek(current=>selectedRunWeek(data,current));
    setRuns(await api('/runs'));
    if(data.active_run && (!run || run.status!=='running'))setRun(await api('/runs/'+data.active_run.id));
  }
  useEffect(()=>{load().catch(e=>setError(e.message));},[]);
  useEffect(()=>{
    if(run?.status==='running')return;
    const refresh=()=>load().catch(e=>setError(e.message));
    const id=setInterval(refresh,5000);
    window.addEventListener('focus',refresh);
    return()=>{clearInterval(id);window.removeEventListener('focus',refresh);};
  },[run?.status,week]);
  useEffect(()=>{if(week) api('/deals?week='+encodeURIComponent(week)+'&q='+encodeURIComponent(search)+'&fit='+fit).then(setDeals).catch(e=>setError(e.message));},[week,search,fit,run?.status]);
  useEffect(()=>{const id=setInterval(()=>setNow(Date.now()),100);return()=>clearInterval(id);},[]);
  useEffect(()=>{
    if(!run || run.status!=='running')return;
    const id=setInterval(async()=>{try{const data=await api('/runs/'+run.id);setRun(data);if(data.status!=='running'){if(clicked)setBrowserSeconds((performance.now()-clicked)/1000);await load();}}catch(e){setError(e.message)}},700);
    return()=>clearInterval(id);
  },[run?.id,run?.status]);
  async function trigger(kind){
    setBusy(true);setError('');setBrowserSeconds(null);const click=performance.now();setClicked(click);
    try{const result=await api('/jobs',{week_id:week,kind,target,collect:kind==='prepare'&&!selectedWeek?.block_count});setRun(await api('/runs/'+result.run_id));setView('run');}catch(e){setError(e.message)}finally{setBusy(false)}
  }
  async function inspect(id){try{setDetail(await api('/deals/'+id))}catch(e){setError(e.message)}}
  async function testGoogle(){setBusy(true);try{setHealth(await api('/health/google'))}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function openRun(id){const data=await api('/runs/'+id);setRun(data);setWeek(data.week_id);setView('run');setBrowserSeconds(null);setClicked(null)}
  const active=run?.status==='running', reviewing=run?.kind==='review', metrics=run?.metrics||{},elapsed=reviewing?(active?(now-new Date(run.started_at).getTime())/1000:run?.elapsed):null;
  const sourceTotals=selectedWeek?.sources||[];
  return <div className="app">
    <aside className="sidebar"><a className="brand" href="#" onClick={e=>e.preventDefault()}><div className="brand-symbol"><Icon icon={Activity}/></div><div>Deal Review<span>METAPROP</span></div></a>
      <nav>{[['run',Activity,'Control Center'],['database',Database,'Deal Database'],['history',History,'Run History'],['eval',FlaskConical,'Evaluation']].map(([key,I,name])=><button key={key} className={view===key?'nav active':'nav'} onClick={()=>{setView(key);if(key==='eval')api('/evaluation').then(setEvaluation)}}><Icon icon={I}/>{name}</button>)}</nav>
      <div className="sidebar-bottom"><span className="dot"/> Local workspace<div>{overview?.model || 'Connecting'}</div><small>Test outputs by default</small></div>
    </aside>
    <main><header className="topbar"><span>MetaProp / {view==='database'?'Deal Database':view==='history'?'Run History':view==='eval'?'Evaluation':'Control Center'}</span><div><Badge value={selectedWeek?.status||'raw'}/><button className="icon-btn" title="Refresh workspace" onClick={()=>load().catch(e=>setError(e.message))}><Icon icon={RefreshCw}/></button></div></header>
      <div className="page"><div className="page-heading"><div><h1>{view==='database'?'Deal Database':view==='history'?'Run History':view==='eval'?'Historical Evaluation':'Weekly Deal Review'}</h1><p>{view==='database'?`${overview?.totals.deals||0} financing records across ${overview?.weeks.length||0} weeks`:view==='history'?'Recorded workflow runs':view==='eval'?'First decisions compared with independently reviewed decisions':'MetaProp investment mandate'}</p></div><div className="week-picker"><label htmlFor="week">IC WEEK</label><select id="week" value={week} disabled={active} onChange={e=>{setWeek(e.target.value);setRun(null)}}>{overview?.weeks.map(w=><option key={w.id} value={w.id}>{w.label}</option>)}</select></div></div>
      {error&&<div className="notice error"><Icon icon={AlertTriangle}/><span>{error}</span><button className="icon-btn" title="Dismiss error" onClick={()=>setError('')}><Icon icon={X}/></button></div>}
      {view==='run'&&<>
        <section className="source-band"><div className="section-title"><h2>Prepared Inputs</h2><button className="text-button" disabled={busy||active} onClick={()=>trigger('prepare')}><Icon icon={RefreshCw}/>{active&&!reviewing?'Preparing':'Prepare Data'}</button></div><div className="sources">{['StrictlyVC','Term Sheet','Pro Rata'].map((source,i)=>{
          const info=sourceTotals.find(s=>s.source===source);
          const status=sourceStatus(info,selectedWeek?.report?.source_availability?.[source]);
          return <div className="source-item" key={source}><div className={'source-icon source-'+i}><Icon icon={Mail}/></div><div><strong>{source}</strong><span>{info?.emails||0} emails / {info?.source_items||0} source items</span></div><span className="source-status" title={status.reason} aria-label={status.reason}><Icon icon={status.state==='captured'?Check:status.state==='not_published'?Minus:AlertTriangle}/></span></div>
        })}</div><div className="input-footer"><span><strong>{selectedWeek?.deal_count||0}</strong> unique financings</span><span><strong>{selectedWeek?.report?.duplicate_mentions||0}</strong> duplicate mentions merged</span><span><strong>{selectedWeek?.report?.missing_fields||0}</strong> documented unavailable fields</span><span>{active&&!reviewing?'Preparation: '+sec((now-new Date(run.started_at).getTime())/1000):selectedWeek?.status==='prepared'?'Prepared '+new Date(selectedWeek.updated_at).toLocaleString():'Preparation pending'}</span></div></section>
        <section className="run-band"><div className="run-top"><div><h2>Review & Publish</h2><div className="target"><Settings2 size={15}/><select aria-label="Publication target" value={target} onChange={e=>setTarget(e.target.value)} disabled={active}><option value="test">Google demo copies</option><option value="local">Local review only</option>{overview?.production_enabled&&<option value="production">Production</option>}</select></div></div><div className="run-actions"><div className="timer"><Clock size={16}/><strong>{elapsed==null?'0.0s':sec(Math.max(elapsed,0))}</strong><span>/ 120s</span></div><button className="primary" disabled={busy||active||selectedWeek?.status!=='prepared'} onClick={()=>trigger('review')}><Icon icon={Play}/>{active?'Running':'Review & Publish'}</button></div></div>
          <div className="pipeline">{stages.map((stage,i)=>{const {done,running,skipped}=stepState(run,metrics,stage,i);return <React.Fragment key={stage}><div className={'pipeline-step '+(done?'done ':running?'current ':'')}><span>{done?<Check size={16}/>:i+1}</span><strong>{['Review','Independent Check','Validate Rows','Publish','Confirm'][i]}</strong><small>{skipped?'Not requested':run?.timings?.[stage]!=null?stageDuration(run.timings[stage]):running?'In progress':'--'}</small></div>{i<4&&<ArrowRight size={16} className="pipeline-arrow"/>}</React.Fragment>})}</div>
          {metrics.review_schedule==='streaming'&&run?.timings?.review_check_overlap!=null&&<small>Decisioning wall time: {sec(run.timings.decisioning)} / Review-check overlap: {sec(run.timings.review_check_overlap)}</small>}
          {metrics.row_completeness&&<small> / Unexplained blanks: {metrics.row_completeness.unexplained_blanks} / Documented unavailable fields: {metrics.row_completeness.documented_unavailable.length}</small>}
          <div className="metric-grid">{[['First reviewed',metrics.first_ready??metrics.companies_reviewed],['Uncertain',metrics.uncertain],['Independently checked',metrics.verified],['Selected',metrics.count],['Decision changes',metrics.changes]].map(([name,n])=><div key={name}><span>{name}</span><strong>{n??'--'}</strong></div>)}</div>
          {run?.status&&run.status!=='running'&&<div className={'result-line '+(run.status==='completed'?'success':'')}><Icon icon={run.status==='completed'?ShieldCheck:run.status==='failed'?AlertTriangle:Check}/><span>{resultMessage(run)}</span><Badge value={run.status}/>{browserSeconds!=null&&<small>Click to visible result: {sec(browserSeconds)}</small>}</div>}
          <RunDetails key={run?.id||'idle'} run={run} inspect={inspect}/>
          <div className="outputs"><h3>Outputs</h3><div><a href={'https://docs.google.com/spreadsheets/d/'+overview?.targets[target==='production'?'production':'test'].sheet+'/edit'} target="_blank" rel="noreferrer"><Database size={18}/><span>Deal Sheet & Stats</span><ExternalLink size={14}/></a><a href={'https://docs.google.com/document/d/'+overview?.targets[target==='production'?'production':'test'].doc+'/edit'} target="_blank" rel="noreferrer"><FileText size={18}/><span>Weekly Roundup</span><ExternalLink size={14}/></a><button className="text-button" onClick={testGoogle} disabled={busy}><ShieldCheck size={16}/>Check Connection</button></div>{health&&<div className={'health '+(health.ready?'success':'error')}><Icon icon={health.ready?Check:AlertTriangle}/>{health.ready?'Google demo copies accessible':health.error}</div>}<small>Linked chart images: manual refresh in Google Docs. Sheet charts update from Stats.</small></div>
        </section>
      </>}
      {view==='database'&&<><div className="table-tools"><div className="search"><Search size={17}/><input aria-label="Search companies" placeholder="Search companies, investors, sectors" value={search} onChange={e=>setSearch(e.target.value)}/></div><select aria-label="Filter fit" value={fit} onChange={e=>setFit(e.target.value)}>{['All','Yes','Maybe','No','Unreviewed'].map(x=><option key={x}>{x}</option>)}</select><span>{deals.length} records</span></div><div className="table-scroll"><table><thead><tr><th>Company</th><th>Sector</th><th>Round</th><th>Amount</th><th>Location</th><th>Sources</th><th>Fit</th><th/></tr></thead><tbody>{deals.map(d=><tr key={d.id} onClick={()=>inspect(d.id)}><td><strong>{d.company}</strong><small>{d.fields.website?.replace(/^https?:\/\//,'').replace(/\/$/,'')||'Website unavailable'}</small></td><td>{d.fields.sector}</td><td>{d.fields.round||'Undisclosed'}</td><td>{d.fields.amount||'Undisclosed'}</td><td>{d.fields.geography||'Not disclosed'}</td><td>{d.sources.join(', ')}</td><td><Badge value={d.decision?.final.fit||'Unreviewed'}/></td><td><ChevronRight size={16}/></td></tr>)}</tbody></table>{!deals.length&&<div className="empty"><Database size={25}/><span>{selectedWeek?.status==='prepared'?'No matching records':'Prepare this week to populate the database'}</span></div>}</div></>}
      {view==='history'&&<div className="table-scroll"><table><thead><tr><th>Started</th><th>Week</th><th>Workflow</th><th>Target</th><th>Status</th><th>Selected</th><th>Time</th><th/></tr></thead><tbody>{runs.map(r=><tr key={r.id} onClick={()=>openRun(r.id)}><td>{new Date(r.started_at).toLocaleString()}</td><td>{r.week_id.split('_').join(' to ')}</td><td>{r.kind}</td><td>{r.target}</td><td><Badge value={r.status}/></td><td>{r.metrics.count??r.metrics.unique_deals??'--'}</td><td>{sec(r.elapsed)}</td><td><ChevronRight size={16}/></td></tr>)}</tbody></table>{!runs.length&&<div className="empty">No runs yet</div>}</div>}
      {view==='eval'&&<><div className="notice"><FlaskConical size={18}/><span>{evaluation?.limitation||'Loading historical labels'}</span></div><div className="metric-grid evaluation">{[['Scored',evaluation?.counts.scored],['Missing / unscored',evaluation?.counts.missing],['First-pass correct',evaluation?.counts.first_correct],['Final correct',evaluation?.counts.final_correct],['Include recall',evaluation?.counts.recall!=null?Math.round(evaluation.counts.recall*100)+'%':'--']].map(([k,v])=><div key={k}><span>{k}</span><strong>{v??'--'}</strong></div>)}</div><div className="table-scroll"><table><thead><tr><th>Company</th><th>Week</th><th>Reference</th><th>First</th><th>Final</th><th>Result</th></tr></thead><tbody>{evaluation?.cases.map((c,i)=><tr key={i}><td>{c.company}</td><td>{c.week}</td><td><Badge value={c.expected_final||c.fit}/></td><td>{c.first||'--'}</td><td>{c.final||'--'}</td><td>{c.status==='unscored'?'Unscored':c.correct?'Correct':'Mismatch'}</td></tr>)}</tbody></table></div></>}
      </div>
    </main>
    {detail&&<DealDetail deal={detail} close={()=>setDetail(null)} onSaved={()=>inspect(detail.id)}/>}
  </div>
}
function RunDetails({run,inspect}){
  const [preferences,setPreferences]=useState({});
  const decisions=run?.decisions||[],metrics=run?.metrics||{};
  const auditOpen=runSectionExpanded(run,'audit',preferences.audit),decisionsOpen=runSectionExpanded(run,'decisions',preferences.decisions);
  function toggle(section){setPreferences(current=>({...current,[section]:!runSectionExpanded(run,section,current[section])}));}
  return <div className="run-details">
    <div className="run-detail-heading"><h3><button id="run-heading-audit" type="button" className="disclosure-button" aria-expanded={auditOpen} aria-controls="run-panel-audit" onClick={()=>toggle('audit')}><ChevronRight size={16}/><History size={15}/><span>Live Audit</span></button></h3></div>
    <div id="run-panel-audit" role="region" aria-labelledby="run-heading-audit" hidden={!auditOpen} className="run-detail-panel audit-band">
      {run?.events?.length?<div className="events" tabIndex={0} aria-label="Audit events">{[...run.events].reverse().map(e=><div key={e.id}><time>{stamp(e.at)}</time><span className="event-stage">{e.stage}</span><p>{e.message}</p>{e.detail?.seconds&&<small>{sec(e.detail.seconds)}</small>}</div>)}</div>:<div className="empty"><History size={24}/><span>{run?'No events yet':'No run selected'}</span></div>}
    </div>
    <div className="run-detail-heading"><h3><button id="run-heading-decisions" type="button" className="disclosure-button" aria-expanded={decisionsOpen} aria-controls="run-panel-decisions" onClick={()=>toggle('decisions')}><ChevronRight size={16}/><ShieldCheck size={15}/><span>Decisions</span><span className="detail-count">{decisions.length}</span></button></h3></div>
    <div id="run-panel-decisions" role="region" aria-labelledby="run-heading-decisions" hidden={!decisionsOpen} className="run-detail-panel decision-band">
      {decisions.length>0?<><div className="decision-summary-line">{metrics.confirmed||0} included / {metrics.uncertain||0} initially uncertain / {metrics.excluded||0} excluded / {metrics.known_investments||0} known investments</div><DecisionTable decisions={decisions} inspect={inspect}/></>:<div className="empty">No decisions yet</div>}
    </div>
  </div>;
}
function DecisionTable({decisions,inspect}){
  const [filter,setFilter]=useState('all');
  const filters=[['all','All'],['uncertain','Initial Uncertain'],['disagreed','Checks disagreed']];
  const visible=decisions.filter(d=>decisionMatchesFilter(d,filter));
  return <>
    <div className="table-tools decision-tools"><label htmlFor="decision-filter">Show</label><select id="decision-filter" aria-label="Filter decisions" value={filter} onChange={e=>setFilter(e.target.value)}>{filters.map(([value,name])=><option key={value} value={value}>{name} ({decisions.filter(d=>decisionMatchesFilter(d,value)).length})</option>)}</select><span aria-live="polite">{visible.length} of {decisions.length} deals</span></div>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Decision records"><table><thead><tr><th>Company</th><th>First</th><th>Independent</th><th>Final</th><th>Confidence</th><th>Reason</th><th/></tr></thead><tbody>{visible.map(d=><tr key={d.deal_id} onClick={()=>inspect(d.deal_id)}><td><strong>{d.company}</strong></td><td><Badge value={d.first_pass.fit}/></td><td>{d.verification?<Badge value={d.verification.fit}/>:'Not routed'}</td><td>{d.final.known_investment?<span className="badge neutral">Portfolio</span>:<Badge value={d.final.fit}/>}</td><td>{Math.round(d.final.confidence*100)}%</td><td className="reason-cell">{d.final.reason}</td><td><ChevronRight size={16}/></td></tr>)}</tbody></table>{!visible.length&&<div className="empty">No decisions match this filter</div>}</div>
  </>;
}
function DealDetail({deal,close,onSaved}){
  const [fit,setFit]=useState('Yes'),[reason,setReason]=useState(''),[saved,setSaved]=useState(false),[error,setError]=useState('');const decision=deal.decisions[0];
  async function save(){try{await api('/feedback',{deal_id:deal.id,fit,reason});setSaved(true);onSaved()}catch(e){setError(e.message)}}
  return <div className="drawer-overlay" onClick={close}><aside className="drawer" onClick={e=>e.stopPropagation()}><header><div><small>FINANCING RECORD</small><h2>{deal.company}</h2></div><button className="icon-btn" title="Close company details" onClick={close}><X size={20}/></button></header><div className="drawer-body">{decision&&<section><h3>Final Decision</h3><div className="decision-summary"><Badge value={decision.final.fit}/><span>{Math.round(decision.final.confidence*100)}% subjective confidence</span>{decision.verification&&<ShieldCheck size={17}/>}</div><p>{decision.final.reason}</p><small>Evidence: {decision.final.evidence_ids.join(', ')}</small></section>}<section><h3>Company & Financing</h3><dl>{Object.entries(deal.fields).map(([key,value])=><React.Fragment key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{value?<>{key.includes('website')||key.includes('link')?<a href={value} target="_blank" rel="noreferrer">{value}<ExternalLink size={12}/></a>:value}</>:<span className="missing">{deal.missing[key]||'Not available'}</span>}</dd></React.Fragment>)}</dl></section><section><h3>Portfolio Resolution</h3><pre>{JSON.stringify(deal.portfolio_match,null,2)}</pre></section><section><h3>Source Evidence</h3>{deal.source_evidence.map(e=><details key={e.id}><summary><Mail size={15}/>{e.source}<small>{e.id}</small></summary><p className="source-text">{e.text}</p></details>)}{deal.evidence.filter(e=>e.kind==='web').map(e=><div className="web-evidence" key={e.id}><a href={e.url} target="_blank" rel="noreferrer">{e.url}<ExternalLink size={13}/></a><blockquote>{e.quote}</blockquote>{e.summary&&<><small>Source context</small><p>{e.summary}</p></>}<small>{e.fields.join(', ')} / {new Date(e.at).toLocaleString()}</small></div>)}</section><section><h3>Correction Memory</h3><div className="correction-form"><select aria-label="Corrected fit" value={fit} onChange={e=>setFit(e.target.value)}>{['Yes','No'].map(v=><option key={v}>{v}</option>)}</select><textarea aria-label="Correction reason" placeholder="Reason for the correction" value={reason} onChange={e=>{setReason(e.target.value);setSaved(false)}}/><button className="secondary" disabled={reason.length<5} onClick={save}><Save size={15}/>{saved?'Saved':'Save Correction'}</button>{error&&<span className="error">{error}</span>}</div>{deal.feedback.map(f=><div className="feedback" key={f.id}><Badge value={f.fit}/><p>{f.reason}</p><small>{new Date(f.at).toLocaleString()}</small></div>)}</section></div></aside></div>
}
createRoot(document.getElementById('root')).render(<App/>);
