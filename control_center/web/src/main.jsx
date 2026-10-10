import React, {useEffect, useId, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, Database, FileText, History, Play, RefreshCw, Search, ShieldCheck, ChevronRight, ChevronDown, X, ExternalLink, Mail, Check, AlertTriangle, Clock, Settings2, Save, ArrowRight, Pencil, Circle, CircleCheck} from 'lucide-react';
import './style.css';
import './control-center.css';
import {canEditReview, canEditSelectionReview, datasetCounts, decisionMatchesFilter, decisionResolution, workflowProgress, workflowCounts, workflowDuration, evidenceGroups, preparationForWeek, preparationForWeeks, resultMessage, runForSelection, runMatchesSelection, runForWeek, runSectionExpanded, runsForWeek, selectedRunWeek, stageDuration} from './workflow-state.js';

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
function Badge({value}) {return <span className={'badge ' + (value === 'Yes' || value === 'completed' || value === 'prepared' ? 'green' : value === 'Maybe' || value === 'running' ? 'amber' : value === 'No' ? 'neutral' : value === 'failed' ? 'red' : 'neutral')}>{label(value)}</span>}

function CompanyFavicon({website,company}){
  const [failed,setFailed]=useState(false),[size,setSize]=useState(20);
  let hostname='';
  try{
    const url=new URL(website?.includes('://')?website:'https://'+website);
    if(['http:','https:'].includes(url.protocol)&&url.hostname.includes('.'))hostname=url.hostname;
  }catch{}
  return <span className="company-favicon" aria-hidden="true">{hostname&&!failed?<img src={'https://www.google.com/s2/favicons?domain='+encodeURIComponent(hostname)+'&sz=128'} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer" onLoad={event=>setSize(Math.min(20,event.currentTarget.naturalWidth,event.currentTarget.naturalHeight))} onError={()=>setFailed(true)}/>:<span>{company?.trim().charAt(0).toUpperCase()||'?'}</span>}</span>;
}

function WeekMultiSelect({weeks,selected,toggle,disabled,label="Choose weeks"}){
  const optionsId=useId();
  const [open,setOpen]=useState(false);
  const container=useRef(null),trigger=useRef(null);
  useEffect(()=>{
    if(!open)return;
    function outside(event){if(!container.current?.contains(event.target))setOpen(false)}
    document.addEventListener('pointerdown',outside);
    return()=>document.removeEventListener('pointerdown',outside);
  },[open]);
  useEffect(()=>{if(disabled)setOpen(false)},[disabled]);
  const chosen=weeks.filter(item=>selected.includes(item.id));
  return <div className="custom-week-select" ref={container} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setOpen(false)}} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();setOpen(false);trigger.current?.focus()}}}>
    <button type="button" className="custom-week-trigger" ref={trigger} disabled={disabled} aria-expanded={open} aria-label={label} aria-controls={optionsId} onClick={()=>setOpen(previous=>!previous)}><span>{chosen.length===1?chosen[0].label:chosen.length?`${chosen.length} weeks selected`:'Select weeks'}</span>{open?<ChevronDown size={16}/>:<ChevronRight size={16}/>}</button>
    {open&&<fieldset className="custom-week-options" id={optionsId}><legend className="sr-only">Choose weeks</legend>{weeks.map(item=><label key={item.id} onMouseDown={event=>{if(event.target.tagName!=='INPUT')event.preventDefault()}} onClick={event=>{if(event.target.tagName!=='INPUT'){event.preventDefault();if(!disabled)toggle(item.id)}}}><input type="checkbox" checked={selected.includes(item.id)} disabled={disabled} onChange={()=>toggle(item.id)}/><span>{item.label}</span></label>)}</fieldset>}
  </div>;
}

function App(){
  const [overview,setOverview]=useState(null),[view,setView]=useState('run'),[week,setWeek]=useState(''),[selectedRun,setRun]=useState(null),[activeRun,setActiveRun]=useState(null),[runs,setRuns]=useState([]),[dealResults,setDealResults]=useState({path:null,records:[]}),[search,setSearch]=useState(''),[fit,setFit]=useState('All'),[detail,setDetail]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[health,setHealth]=useState(null),[target,setTarget]=useState('test'),[now,setNow]=useState(Date.now()),[clicked,setClicked]=useState(null),[browserSeconds,setBrowserSeconds]=useState(null);
  const [databaseWeeks,setDatabaseWeeks]=useState(null);
  const [customWeeks,setCustomWeeks]=useState(null),[datasetResult,setDatasetResult]=useState({key:null,value:null});
  const pendingRef=useRef(null);
  const [historyRun,setHistoryRun]=useState(null);
  const historyRequest=useRef(0);
  const [showAddressed,setShowAddressed]=useState(false),[missingOpen,setMissingOpen]=useState(false),[repairField,setRepairField]=useState(null),[confirmAction,setConfirmAction]=useState(null);
  const [savedReview,setSavedReview]=useState(null),[reviewRevision,setReviewRevision]=useState(0),[pendingPreparation,setPendingPreparation]=useState(null),[preparationRun,setPreparationRun]=useState(null);
  pendingRef.current=pendingPreparation;
  const loadRequest=useRef(0),jobPending=useRef(false);
  const selectedWeek=overview?.weeks.find(w=>w.id===week);
  const datasetWeekIds=customWeeks===null?(week?[week]:[]):(overview?.weeks||[]).filter(item=>customWeeks.includes(item.id)).map(item=>item.id);
  const customDataset=datasetWeekIds.length>1;
  const databaseWeekIds=databaseWeeks===null?(overview?.weeks||[]).map(item=>item.id):databaseWeeks;
  const datasetKey=datasetWeekIds.slice().sort().join('|');
  const run=runForSelection(selectedRun,activeRun,datasetWeekIds,savedReview);
  const databaseQuery=new URLSearchParams({q:search,fit});databaseWeekIds.forEach(id=>databaseQuery.append('week_ids',id));
  const dealsPath=view==='database'?'/dataset/deals?'+databaseQuery:'/deals?week='+encodeURIComponent(week)+'&q='+encodeURIComponent(search)+'&fit='+encodeURIComponent(fit);
  const deals=dealResults.path===dealsPath?dealResults.records:[];
  const dealsLoading=dealResults.path!==dealsPath;
  function setDeals(records){setDealResults({path:dealsPath,records})}
  const workspace=overview?.workspace||{name:'Local workspace',allow_preparation:true,targets:['test','local','production'],actions:[]};
  const historyRuns=runs.filter(item=>(item.metrics?.week_ids||[item.week_id]).some(id=>datasetWeekIds.includes(id)));
  async function load(){
    const request=++loadRequest.current;
    let [data,history]=await Promise.all([api('/overview'),api('/runs')]);
    let current=data.active_run?await api('/runs/'+data.active_run.id):null;
    const finishedPreparation=current&&current.kind!=='review'&&current.status!=='running'?current:null;
    if(current&&current.status!=='running'){data=await api('/overview');current=data.active_run?await api('/runs/'+data.active_run.id):null;}
    if(request!==loadRequest.current||jobPending.current)return;
    setOverview(data);setRuns(history);setWeek(previous=>selectedRunWeek(data,previous));
    setActiveRun(current?.status==='running'?current:null);
    if(finishedPreparation)setPreparationRun(finishedPreparation);else if(current&&current.kind!=='review')setPreparationRun(current);
    if(current)setRun(previous=>previous?.kind==='review'&&previous.id===current.id?current:previous);
    if(!pendingRef.current?.week_ids)setPendingPreparation(null);setReviewRevision(previous=>previous+1);
  }
  useEffect(()=>{
    if(!customDataset||!datasetWeekIds.length)return;
    let cancelled=false;
    const query=new URLSearchParams();datasetWeekIds.forEach(id=>query.append('week_ids',id));
    api('/dataset?'+query).then(value=>{if(!cancelled){setDatasetResult({key:datasetKey,value});if(pendingRef.current?.week_ids&&pendingRef.current.run_id&&activeRun?.status!=='running')setPendingPreparation(null)}}).catch(error=>{if(!cancelled){setDatasetResult({key:datasetKey,value:null});setError(error.message);if(pendingRef.current?.week_ids&&pendingRef.current.run_id&&activeRun?.status!=='running')setPendingPreparation(null)}});
    return()=>{cancelled=true};
  },[customDataset,datasetKey,reviewRevision,activeRun?.status]);
  useEffect(()=>{
    if(!datasetWeekIds.length){setSavedReview(null);return;}
    let cancelled=false;
    const query=new URLSearchParams();datasetWeekIds.forEach(id=>query.append('week_ids',id));
    const path=datasetWeekIds.length>1?'/dataset/latest-review?'+query:'/weeks/'+encodeURIComponent(datasetWeekIds[0])+'/latest-review';
    api(path).then(data=>{if(!cancelled)setSavedReview(data)}).catch(e=>{if(!cancelled)setError(e.message)});
    return()=>{cancelled=true};
  },[datasetKey,reviewRevision]);
  useEffect(()=>{load().catch(e=>setError(e.message));},[]);
  useEffect(()=>{
    if(view!=='run-detail'||historyRun?.status!=='running')return;
    let cancelled=false,polling=false;
    const id=setInterval(async()=>{if(polling)return;polling=true;try{const next=await api('/runs/'+historyRun.id+'/history');if(!cancelled)setHistoryRun(next)}catch(error){if(!cancelled)setError(error.message)}finally{polling=false}},1000);
    return()=>{cancelled=true;clearInterval(id)};
  },[view,historyRun?.id,historyRun?.status]);
  useEffect(()=>{
    if(activeRun?.status==='running')return;
    const refresh=()=>load().catch(e=>setError(e.message));
    const id=setInterval(refresh,5000);
    window.addEventListener('focus',refresh);
    return()=>{clearInterval(id);window.removeEventListener('focus',refresh);};
  },[activeRun?.status,week]);
  useEffect(()=>{
    if(!week)return;
    let cancelled=false;
    api(dealsPath).then(data=>{if(!cancelled)setDeals(data)}).catch(e=>{if(!cancelled){setDeals([]);setError(e.message)}});
    return()=>{cancelled=true};
  },[dealsPath,run?.status,reviewRevision]);
  useEffect(()=>{const id=setInterval(()=>setNow(Date.now()),100);return()=>clearInterval(id);},[]);
  useEffect(()=>{
    if(activeRun?.status!=='running')return;
    let cancelled=false,polling=false;
    const id=setInterval(async()=>{
      if(polling)return;polling=true;
      try{
        const data=await api('/runs/'+activeRun.id);
        if(cancelled)return;
        if(data.kind!=='review'){
          setPreparationRun(data);
          if(data.status!=='running')setPendingPreparation({week_id:data.week_id,run_id:data.id,...(data.kind==='prepare_batch'?{week_ids:data.metrics.week_ids}:{})});
        }
        setActiveRun(data);setRun(previous=>previous?.kind==='review'&&previous.id===data.id?data:previous);
        if(data.status!=='running'){
          if(clicked&&data.kind==='review')setBrowserSeconds({run_id:data.id,seconds:(performance.now()-clicked)/1000});
          await load();
        }
      }catch(e){if(!cancelled)setError(e.message)}finally{polling=false}
    },700);
    return()=>{cancelled=true;clearInterval(id)};
  },[activeRun?.id,activeRun?.status]);
  async function trigger(kind){
    ++loadRequest.current;jobPending.current=true;
    setBusy(true);setError('');setBrowserSeconds(null);const click=performance.now();setClicked(click);
    if(kind==='prepare')setPendingPreparation({week_id:week,...(customDataset?{week_ids:datasetWeekIds}:{})});
    try{
      const result=kind==='prepare'&&customDataset?await api('/dataset/prepare',{week_ids:datasetWeekIds,collect:true}):await api('/jobs',{week_id:customDataset?datasetWeekIds[0]:week,kind,target,collect:kind==='prepare',...(kind==='review'&&customDataset?{week_ids:datasetWeekIds}:{})});
      if(!result.run_id){pendingRef.current=null;setPendingPreparation(null);jobPending.current=false;await load();return}
      const current=await api('/runs/'+result.run_id);
      if(kind==='review')setRun(current);else setPreparationRun(current);
      setActiveRun(current);setPendingPreparation(null);setView('run');
    }catch(e){setError(e.message);pendingRef.current=null;setPendingPreparation(null);jobPending.current=false;try{await load()}catch{}}finally{jobPending.current=false;setBusy(false)}
  }
  function chooseReviewWeek(id){setWeek(id);setRun(null);setDetail(null);setMissingOpen(false);setPreparationRun(null)}
  function selectDatasetWeeks(ids){setCustomWeeks(ids);setMissingOpen(false);setRun(null);setDetail(null);if(ids.length&&!ids.includes(week))chooseReviewWeek(ids[0])}
  function toggleDatasetWeek(id){selectDatasetWeeks(datasetWeekIds.includes(id)?datasetWeekIds.filter(value=>value!==id):[...datasetWeekIds,id])}
  function toggleDatabaseWeek(id){setDatabaseWeeks(databaseWeekIds.includes(id)?databaseWeekIds.filter(value=>value!==id):[...databaseWeekIds,id]);setDetail(null)}
  async function inspect(id,field=null){try{setRepairField(field);setDetail(await api('/deals/'+id+(!field&&view!=='database'&&run?.kind==='review'?'?run_id='+run.id:'')))}catch(e){setError(e.message)}}
  async function addressMissing(item){
    setBusy(true);setError('');
    try{
      await api('/deals/'+encodeURIComponent(item.deal_id)+'/missing-addressed',{field:item.field,addressed:!item.addressed,week_ids:datasetWeekIds});
      await load();
    }catch(error){setError(error.message)}finally{setBusy(false)}
  }
  async function testGoogle(){setBusy(true);try{setHealth(await api('/health/google'))}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function openRun(id){const request=++historyRequest.current;try{const data=await api('/runs/'+id+'/history');if(request!==historyRequest.current)return;setHistoryRun(data);setWeek(data.week_id);setDetail(null);setError('');setView('run-detail')}catch(error){if(request===historyRequest.current)setError(error.message)}}
  async function inspectHistorical(id){try{setDetail(await api('/runs/'+historyRun.id+'/deals/'+id));setRepairField(null)}catch(error){setError(error.message)}}
  function currentWeek(){const ids=historyRun.metrics?.week_ids||[historyRun.week_id];setCustomWeeks(ids);setWeek(historyRun.week_id);setRun(null);setDetail(null);setPreparationRun(null);setMissingOpen(false);setView('run')}
  async function correctDecision(decision,nextFit,reason,source_url='',evidence_id=''){
    setBusy(true);
    try{const updated=await api('/runs/'+run.id+'/decisions/'+encodeURIComponent(decision.deal_id)+'/correction',{fit:nextFit,reason,source_url,evidence_id,expected_final:decision.final});setRun(updated);try{setRuns(await api('/runs'));setDeals(await api(dealsPath));}catch(e){setError(e.message)}}
    finally{setBusy(false)}
  }
  async function refreshOutputs(){
    setBusy(true);setError('');
    try{setRun(await api('/runs/'+run.id+'/publish-corrections',{}));}
    catch(e){setError(e.message);setRun(await api('/runs/'+run.id));}
    finally{setBusy(false)}
  }
  async function saveRecordEdits(deal,changes,decisionEdit,note,source_url,evidence_id){
    setBusy(true);setError('');
    let fresh=deal,fieldsSaved=false;
    try{
      if(Object.keys(changes).length){const result=await api('/deals/'+deal.id+'/correction',{changes,reason:note,source_url,evidence_id,expected_fields:deal.editable_fields,run_id:deal.editable_run_id});fieldsSaved=true;if(result.run_id)setRun(await api('/runs/'+result.run_id));fresh=await api('/deals/'+deal.id);setDetail(fresh)}
      if(decisionEdit){const result=await api('/deals/'+deal.id+'/decision',{...decisionEdit,expected_final:deal.review_decision?.final||deal.decisions[0]?.final||null,expected_fields:fresh.editable_fields});if(result.run_id)setRun(await api('/runs/'+result.run_id))}
      setRepairField(null);setDetail(await api('/deals/'+deal.id));await load();setDeals(await api(dealsPath));
    }catch(error){if(fieldsSaved)throw new Error('Company details saved. '+error.message);throw error}
    finally{setBusy(false)}
  }
  async function recheckDeal(deal){
    setBusy(true);setError('');
    try{setRun(await api('/runs/'+deal.editable_run_id+'/recheck/'+deal.id,{}));setDetail(await api('/deals/'+deal.id));await load();}catch(e){setError(e.message)}finally{setBusy(false)}
  }
  async function workspaceAction(action,confirmed=false){
    if(action.confirmation&&!confirmed){setConfirmAction(action);return;}
    setBusy(true);setError('');
    try{setRun(await api(action.endpoint,{run_id:run?.id}));setDetail(null);await load();}
    catch(e){setError(e.message);if(run)setRun(await api('/runs/'+run.id));}finally{setBusy(false)}
  }
  const active=activeRun?.status==='running'||Boolean(pendingPreparation), viewingActive=run?.status==='running', preparing=preparationForWeek(activeRun,pendingPreparation,week), reviewing=run?.kind==='review', metrics=run?.metrics||{},elapsed=reviewing?(viewingActive?(now-new Date(run.started_at).getTime())/1000:run?.elapsed):null;
  const editableReview=!active&&!pendingPreparation&&canEditSelectionReview(run,runs,overview?.weeks||[],datasetWeekIds);
  const datasetPreparing=preparationForWeeks(activeRun,pendingPreparation,datasetWeekIds);
  const customSummary=datasetResult.key===datasetKey?datasetResult.value:null;
  const dataset=customDataset?(customSummary?{...customSummary,report:{duplicate_mentions:customSummary.duplicate_mentions}}:null):datasetWeekIds.length?selectedWeek:null;
  const counts=datasetCounts(dataset,datasetPreparing);
  const dataPrepared=dataset?.status==='prepared'&&!datasetPreparing;
  const preparationMatches=value=>value&&value.kind!=='review'&&(value.metrics?.week_ids||[value.week_id]).some(id=>datasetWeekIds.includes(id));
  const preparation=preparationMatches(activeRun)?activeRun:preparationMatches(preparationRun)?preparationRun:null;
  return <div className="app">
    <aside className="sidebar"><a className="brand" href="#" onClick={e=>e.preventDefault()}><div className="brand-symbol"><Icon icon={Activity}/></div><div>Deal Review<span>METAPROP</span></div></a>
      <nav>{[['run',Activity,'Control Center'],['database',Database,'Deal Database'],['history',History,'Run History']].map(([key,I,name])=><button key={key} className={view===key||(key==='history'&&view==='run-detail')?'nav active':'nav'} onClick={()=>{++historyRequest.current;setDetail(null);setView(key)}}><Icon icon={I}/>{name}</button>)}</nav>
      <div className="sidebar-bottom"><span className="dot"/> {workspace.name}<div>{overview?.model || 'Connecting'}</div><small>Test outputs by default</small></div>
    </aside>
    <main><header className="topbar"><span>MetaProp / {view==='database'?'Deal Database':view==='history'?'Run History':view==='run-detail'?'Run Details':'Control Center'}</span></header>
      <div className={'page'+(view==='run'?' control-center':'')}><div className="page-heading"><div><h1>{view==='database'?'Deal Database':view==='history'?'Run History':view==='run-detail'?'Run Details':'Weekly Deal Review'}</h1><p>{view==='database'? (dealsLoading?'Loading financings…':`${deals.length} unique ${deals.length===1?'financing':'financings'} across ${databaseWeekIds.length} ${databaseWeekIds.length===1?'week':'weeks'}`):view==='history'?'Recorded workflow runs':view==='run-detail'?'Read-only historical record':'MetaProp investment mandate'}</p></div>{view!=='run-detail'&&<div className="week-picker"><span className="week-selector-label">IC WEEKS</span><WeekMultiSelect weeks={overview?.weeks||[]} selected={view==='database'?databaseWeekIds:datasetWeekIds} toggle={view==='database'?toggleDatabaseWeek:toggleDatasetWeek} disabled={busy||(view==='run'&&active)} label={view==='database'?'Filter weeks':'Choose weeks'}/></div>}</div>
      {activeRun?.status==='running'&&activeRun.week_id!==week&&!(view==='run'&&runMatchesSelection(activeRun,datasetWeekIds))&&<div className="notice background-run"><Clock size={17}/><span>{activeRun.kind==='review'?'Reviewing':'Preparing'} {activeRun.metrics?.week_ids?.length>1?activeRun.metrics.week_ids.length+' selected weeks':overview?.weeks.find(w=>w.id===activeRun.week_id)?.label||activeRun.week_id} in the background</span><button className="text-button" onClick={()=>{const ids=activeRun.metrics?.week_ids||[activeRun.week_id];setCustomWeeks(ids);setWeek(activeRun.week_id);setRun(activeRun.kind==='review'?activeRun:null);setView('run');setDetail(null);setMissingOpen(false)}}>View progress<ArrowRight size={15}/></button></div>}
      {error&&<div className="notice error"><Icon icon={AlertTriangle}/><span>{error}</span><button className="icon-btn" title="Dismiss error" onClick={()=>setError('')}><Icon icon={X}/></button></div>}
      {view==='run-detail'&&historyRun&&<HistoricalRunView key={historyRun.id} run={historyRun} targets={overview?.targets||{}} inspect={inspectHistorical} back={()=>setView('history')} currentWeek={currentWeek}/>}
      {view==='run'&&<>
        <section className="dataset-band"><div className="section-title"><h2>Weekly Deal Dataset</h2><button className={'primary dataset-prepare'+(dataPrepared?' prepared':'')} disabled={!dataset||!datasetWeekIds.length||!workspace.allow_preparation||dataPrepared||busy||active||Boolean(pendingPreparation)} title={!workspace.allow_preparation&&!dataPrepared?'Prepare data in the main workspace':undefined} onClick={()=>trigger('prepare')}><Icon icon={datasetPreparing?RefreshCw:dataPrepared?Check:Play}/>{datasetPreparing?'Preparing':dataPrepared?'Data Prepared':'Prepare Dataset'}</button></div>
          <div className="dataset-summary"><div><span>Unique financings</span><strong>{customDataset?(counts.available?dataset?.deal_count:'--'):datasetWeekIds.length?selectedWeek?.deal_count??0:0}</strong></div><div><span>Duplicates merged</span><strong className={datasetPreparing?'preparing-value':''}>{counts.duplicates}</strong></div><div><span>Remaining Missing Fields</span><button className="missing-count" aria-label="View Remaining Missing Fields" aria-expanded={counts.available&&missingOpen} onClick={()=>setMissingOpen(!missingOpen)} disabled={!counts.available||!(dataset?.remaining_missing_count||dataset?.addressed_missing_fields?.length)}><strong className={datasetPreparing?'preparing-value':''}>{counts.missing}</strong>{counts.available&&(missingOpen?<ChevronDown size={17}/>:<ChevronRight size={17}/>)}</button></div></div>
          {counts.available&&missingOpen&&<div className="missing-panel table-scroll" role="region" aria-label="Remaining Missing Fields"><table><thead><tr><th>Company</th><th>Field</th>{!customDataset&&<th>Selection</th>}<th>Why unresolved</th><th><label className="missing-toggle"><input type="checkbox" checked={showAddressed} onChange={event=>setShowAddressed(event.target.checked)}/>Show addressed</label></th></tr></thead><tbody>{[...(dataset?.remaining_missing_fields||[]),...(showAddressed?dataset?.addressed_missing_fields||[]:[])].map(item=><tr key={item.deal_id+item.field} className={item.addressed?'missing-addressed':''}><td><strong>{item.company}</strong></td><td>{fieldName(item.field)}</td>{!customDataset&&<td>{editableReview?(run.decisions.find(d=>d.deal_id===item.deal_id)?.final.fit==='Yes'?'Included':'Excluded'):'Not reviewed'}</td>}<td>{item.reason}</td><td><div className="missing-actions"><button className="icon-btn" title={'Edit '+fieldName(item.field)+' for '+item.company} aria-label={'Edit '+fieldName(item.field)+' for '+item.company} disabled={busy||active} onClick={()=>inspect(item.deal_id,item.field)}><Pencil size={15}/></button><button className="icon-btn" title={(item.addressed?'Reopen ':'Mark addressed: ')+fieldName(item.field)+' for '+item.company} aria-label={(item.addressed?'Reopen ':'Mark addressed: ')+fieldName(item.field)+' for '+item.company} aria-pressed={Boolean(item.addressed)} disabled={busy||active} onClick={()=>addressMissing(item)}>{item.addressed?<CircleCheck size={16} className="addressed-symbol"/>:<Circle size={16}/>}</button></div></td></tr>)}</tbody></table>{!dataset?.remaining_missing_count&&!(showAddressed&&dataset?.addressed_missing_fields?.length)&&<div className="empty">No remaining missing fields</div>}</div>}
          {preparation&&<PreparationAudit key={preparation.id} run={preparation}/>}
        </section>
        <section className="run-band"><div className="run-top"><div><h2>Reporting</h2><div className="target"><Settings2 size={15}/><select aria-label="Publication target" value={target} onChange={e=>setTarget(e.target.value)} disabled={active||busy}><option value="test">Google demo copies</option><option value="local">Local review only</option>{overview?.production_enabled&&<option value="production">Production</option>}</select></div></div><div className="run-actions"><div className="timer"><Clock size={16}/><strong>{elapsed==null?'0.0s':sec(Math.max(elapsed,0))}</strong></div><button className="primary" disabled={busy||active||!dataPrepared||!datasetWeekIds.length} onClick={()=>trigger('review')}><Icon icon={Play}/>{viewingActive?'Running':active?'Run in progress':'Review & Publish'}</button></div></div>
          <ReportingPipeline run={run?.metrics?.decision_workflow?run:null}/>
          {run&&['completed','local_completed'].includes(run.status)&&!run.metrics?.decision_workflow&&<p className="previous-workflow-note">This saved run used the previous review flow. New runs use two parallel reviewers and adjudication.</p>}
          <RunDetails key={run?.id||'idle'} run={run} inspect={inspect} correctDecision={correctDecision} editable={editableReview} busy={busy}/>
          {run?.status&&run.status!=='running'&&<div className={'result-line '+(run.status==='completed'&&!metrics.outputs_dirty?'success':'')}><Icon icon={metrics.outputs_dirty||run.status==='failed'?AlertTriangle:run.status==='completed'?ShieldCheck:Check}/><span>{resultMessage(run)}</span><Badge value={metrics.outputs_dirty?'Refresh pending':run.status}/>{browserSeconds?.run_id===run.id&&<small>Click to visible result: {sec(browserSeconds.seconds)}</small>}</div>}
          {metrics.pending_rechecks?.length>0&&<div className="notice"><ShieldCheck size={17}/><span>{metrics.pending_rechecks.length} companies need their decision confirmed or rechecked after factual edits.</span></div>}
          <div className="outputs"><h3>Outputs</h3>{metrics.outputs_dirty&&<div className="correction-pending"><span>Corrections saved locally; outputs need refreshing.</span><button className="secondary" onClick={refreshOutputs} disabled={busy||active||metrics.pending_rechecks?.length>0}><RefreshCw size={15}/>{busy?'Refreshing':'Refresh Outputs'}</button></div>}<div><a href={'https://docs.google.com/spreadsheets/d/'+overview?.targets[(run?.target||target)==='production'?'production':'test'].sheet+'/edit'} target="_blank" rel="noreferrer"><Database size={18}/><span>Deal Sheet & Stats</span><ExternalLink size={14}/></a><a href={'https://docs.google.com/document/d/'+overview?.targets[(run?.target||target)==='production'?'production':'test'].doc+'/edit'} target="_blank" rel="noreferrer"><FileText size={18}/><span>Weekly Roundup</span><ExternalLink size={14}/></a><button className="text-button" onClick={testGoogle} disabled={busy}><ShieldCheck size={16}/>Check Connection</button>{workspace.actions.map(action=><button key={action.endpoint} className="text-button" disabled={busy||active||(action.requires_run&&(!editableReview||!['completed','local_completed'].includes(run?.status)))} onClick={()=>workspaceAction(action)}><RefreshCw size={15}/>{action.label}</button>)}</div>{health&&<div className={'health '+(health.ready?'success':'error')}><Icon icon={health.ready?Check:AlertTriangle}/>{health.ready?'Google copies accessible':health.error}</div>}<small>Linked chart images: manual refresh in Google Docs. Sheet charts update from Stats.</small></div>
        </section>
      </>}
      {view==='database'&&<><div className="table-tools"><div className="search"><Search size={17}/><input aria-label="Search companies" placeholder="Search companies, investors, sectors" value={search} onChange={e=>setSearch(e.target.value)}/></div><select aria-label="Filter fit" value={fit} onChange={e=>setFit(e.target.value)}>{['All','Yes','No','Unreviewed'].map(x=><option key={x} value={x}>{x==='All'?'All fit':x}</option>)}</select><span>{dealsLoading?'Loading…':deals.length+' records'}</span></div><div className="table-scroll"><table className="database-table"><thead><tr><th>Company</th><th>Deal Date</th><th>Sector</th><th>Round</th><th>Amount</th><th>Location</th><th>Sources</th><th>Fit</th><th/></tr></thead><tbody>{deals.map(d=><tr key={d.id} onClick={()=>inspect(d.id)}><td><div className="database-company"><CompanyFavicon key={d.fields.website||''} website={d.fields.website} company={d.company}/><div><strong>{d.company}</strong><small>{d.fields.website?.replace(/^https?:\/\//,'').replace(/\/$/,'')||'Website unavailable'}</small></div></div></td><td className="deal-date">{d.fields.deal_date||'Unresolved'}</td><td>{d.fields.sector}</td><td>{d.fields.round||'Undisclosed'}</td><td>{d.fields.amount||'Undisclosed'}</td><td>{d.fields.geography||'Not disclosed'}</td><td className="source-count">{new Set(d.sources).size}</td><td><Badge value={d.decision?.final.fit||'Unreviewed'}/></td><td><ChevronRight size={16}/></td></tr>)}</tbody></table>{!deals.length&&<div className="empty"><Database size={25}/><span role="status">{dealsLoading?'Loading deals…':'No matching records'}</span></div>}</div></>}
      {view==='history'&&<div className="table-scroll"><table><thead><tr><th>Started</th><th>Week</th><th>Workflow</th><th>Target</th><th>Status</th><th>Selected</th><th>Time</th><th/></tr></thead><tbody>{historyRuns.map(r=><tr key={r.id} onClick={()=>openRun(r.id)}><td>{new Date(r.started_at).toLocaleString()}</td><td>{r.week_id.split('_').join(' to ')}</td><td>{r.kind}</td><td>{r.target}</td><td><Badge value={r.status}/></td><td>{r.historical_selected_count??r.metrics.count??r.metrics.unique_deals??'--'}</td><td>{sec(r.elapsed)}</td><td><ChevronRight size={16}/></td></tr>)}</tbody></table>{!historyRuns.length&&<div className="empty">No runs for the selected weeks</div>}</div>}
      </div>
    </main>
    {detail&&<DealDetail key={detail.id} deal={detail} close={()=>{setDetail(null);setRepairField(null)}} save={saveRecordEdits} recheck={recheckDeal} options={overview?.field_options||{}} repairField={repairField} clearRepair={()=>setRepairField(null)} busy={view==='run-detail'?false:busy||active} pending={detail.pending_recheck??run?.metrics?.pending_rechecks?.includes(detail.id)}/>}
    {confirmAction&&<EditorFrame title={confirmAction.label} close={()=>setConfirmAction(null)} saving={busy}><p className="confirmation-copy">{confirmAction.confirmation}</p><footer className="confirmation-actions"><button className="secondary" disabled={busy} onClick={()=>setConfirmAction(null)}>Cancel</button><button className="primary" disabled={busy} onClick={async()=>{await workspaceAction(confirmAction,true);setConfirmAction(null)}}><RefreshCw size={15}/>{busy?'Applying':confirmAction.label}</button></footer></EditorFrame>}
  </div>
}
function ReportingPipeline({run}){
  const metrics=run?.metrics||{},timings=run?.timings||{},steps=workflowProgress(run),counts=workflowCounts(run);
  return <div className="pipeline dual-pipeline" aria-label="Review workflow">{steps.map((step,i)=>{const duration=workflowDuration(timings,step.key);return <React.Fragment key={step.key}>
    <div className={'pipeline-step '+(step.done?'done ':step.running?'current ':'')}>
      <span>{step.done?<Check size={16}/>:i+1}</span><strong>{step.name}</strong>
      <small>{step.skipped?'Not requested':step.running?'In progress':duration!=null?stageDuration(duration):'--'}</small>
      <div className="stage-counts">
        {step.key==='dual_review'&&metrics.reviewer_a_ready!=null&&<div>{counts.reviewed}/{counts.total} reviewed</div>}
        {step.key==='adjudication'&&metrics.adjudicated!=null&&<><div>{counts.resolved}/{counts.adjudicated} adjudicated</div>{metrics.researched>0&&<div>{counts.researchReady}/{counts.researched} researched</div>}</>}
        {step.key==='validation'&&metrics.count!=null&&<div>{metrics.count} selected</div>}
      </div>
    </div>{i<steps.length-1&&<ArrowRight size={16} className="pipeline-arrow"/>}
  </React.Fragment>})}</div>;
}

function ReviewComparison({decision}){
  const resolution=decision.final.review_resolution;
  if(!resolution)return null;
  return <div className="review-comparison"><small>{decisionResolution(decision)}{decisionMatchesFilter(decision,'disagreed')?' · Original reviewers disagreed':''}</small>
    {[['Reviewer 1',decision.first_pass],['Reviewer 2',decision.verification]].map(([name,answer])=>answer&&<div key={name}><strong>{name}</strong><Badge value={answer.fit}/><p>{answer.reason}</p></div>)}
    {resolution.adjudication&&<div><strong>Adjudicator</strong><Badge value={resolution.adjudication.fit}/><p>{resolution.adjudication.reason}</p></div>}
    {resolution.research?.length>0&&<details><summary>Targeted research ({resolution.research.length})</summary>{resolution.research.map((finding,i)=><div key={i}><strong>{finding.question}</strong><p>{finding.answer}</p>{finding.source_url&&<a href={finding.source_url} target="_blank" rel="noreferrer">Supporting source<ExternalLink size={12}/></a>}</div>)}</details>}
  </div>;
}

function HistoricalRunView({run,targets,inspect,back,currentWeek}){
  const metrics=run.metrics||{},history=run.historical||{},review=run.kind==='review';
  const names={review:'Review',prepare:'Preparation',prepare_batch:'Batch Preparation',resume:'Resumed preparation',enrich:'Enrichment',prepare_normalization:'Normalization',source_repair:'Source repair',rates:'Exchange rates',reconcile:'Reconciliation',usd_refresh:'USD refresh',amount_correction:'Amount correction',publication_repair:'Publication repair'};
  const targetName=run.target==='local'?'Local only':run.target==='production'?'Production':'Google demo copies';
  const statistics=review&&metrics.decision_workflow?[['Reviewer 1',metrics.reviewer_a_ready],['Reviewer 2',metrics.reviewer_b_ready],['Disagreements',metrics.disagreements],['Adjudicated',metrics.adjudicated],['Selected',metrics.count]]:review?[['First reviewed',metrics.first_ready??metrics.companies_reviewed],['Uncertain',metrics.uncertain],['Independently checked',metrics.verified],['Selected',metrics.count],['Decision changes',metrics.changes]]:[['Source items',metrics.source_items],['Unique financings',metrics.unique_deals],['Duplicates merged',metrics.duplicate_mentions],['Missing fields',metrics.missing_fields]];
  const destination=targets[run.target];
  return <div className="historical-run">
    <div className="history-navigation"><button type="button" className="text-button" onClick={back}><History size={15}/>Back to Run History</button><button type="button" className="text-button" onClick={currentWeek}>{run.metrics?.week_ids?.length>1?'View Current Selection':'View Current Week'}<ArrowRight size={15}/></button></div>
    <section className="history-summary"><div className="section-title"><h2>{names[run.kind]||run.kind}</h2><Badge value={run.status}/></div><dl><div><dt>IC week</dt><dd>{(run.metrics?.week_ids||[run.week_id]).map(id=>id.split('_').join(' to ')).join(', ')}</dd></div><div><dt>Started</dt><dd>{new Date(run.started_at).toLocaleString()}</dd></div><div><dt>Target</dt><dd>{targetName}</dd></div><div><dt>Recorded time</dt><dd>{sec(run.elapsed)}</dd></div><div><dt>Run ID</dt><dd>{run.id}</dd></div></dl><small>{history.result_version==='original_completed'?'Original completed results':'Saved results for this run'}{run.status==='running'?' · Run in progress':''}</small>{run.error&&<div className="health error">{run.error}</div>}</section>
    <section className="history-results"><h2>Recorded Results</h2>{review&&metrics.decision_workflow&&<ReportingPipeline run={run}/>}{(!review||!metrics.decision_workflow)&&<div className="metric-grid">{statistics.map(([name,value])=><div key={name}><span>{name}</span><strong>{value??'--'}</strong></div>)}</div>}{Object.keys(run.timings||{}).length>0&&<div className="history-timings">{Object.entries(run.timings).filter(([name])=>metrics.decision_workflow?['startup','decisioning'].includes(name):['startup',...stages,'decisioning'].includes(name)).map(([name,value])=><div key={name}><span>{({dual_review:'Parallel reviews',comparison:'Compare',adjudication:'Adjudicate',research:'Targeted research',final_resolution:'Final resolution',startup:'Startup',review:'Review',verification:'Independent Check',validation:'Validate Rows',publication:'Publish',confirmation:'Confirm',decisioning:'Decisioning'})[name]}</span><strong>{stageDuration(value)}</strong></div>)}</div>}</section>
    <section className="history-inputs"><h3>Company Snapshot</h3><p>{history.snapshot_message||'Historical company snapshot unavailable.'}{history.snapshot_available&&<> {history.snapshot_count} company records.</>}</p></section>
    {review?<RunDetails key={run.id} run={run} inspect={history.snapshot_available?inspect:null} editable={false} busy={false} historical/>:<div className="run-details"><details className="history-audit"><summary><History size={15}/>Run Audit</summary><AuditEvents run={run}/></details></div>}
    {run.later_events?.length>0&&<details className="history-audit later-corrections"><summary><History size={15}/>Later Corrections ({run.later_events.length})</summary><AuditEvents run={{events:run.later_events}} label="Later correction events"/></details>}
    {run.package&&<section className="outputs history-outputs"><h3>Saved Output Package</h3><p>{run.package.rows?.length??0} selected rows recorded for this run.</p>{run.package.rows?.length>0&&<details className="history-audit"><summary>View saved rows</summary><div className="table-scroll"><table><thead><tr>{['Company','Round','Amount','Geography','Fit'].map(name=><th key={name}>{name}</th>)}</tr></thead><tbody>{run.package.rows.map((row,index)=><tr key={index}>{[3,5,6,10,13].map(column=><td key={column}>{row[column]}</td>)}</tr>)}</tbody></table></div></details>}{run.target!=='local'&&destination&&(destination.sheet||destination.doc)&&<div className="live-output-links"><small>Live outputs · these files may contain a later publication.</small><div>{destination.sheet&&<a href={'https://docs.google.com/spreadsheets/d/'+destination.sheet+'/edit'} target="_blank" rel="noreferrer"><Database size={17}/>Live Deal Sheet & Stats<ExternalLink size={13}/></a>}{destination.doc&&<a href={'https://docs.google.com/document/d/'+destination.doc+'/edit'} target="_blank" rel="noreferrer"><FileText size={17}/>Live Weekly Roundup<ExternalLink size={13}/></a>}</div></div>}</section>}
  </div>;
}
function AuditEvents({run,label='Audit events'}){
  return run?.events?.length?<div className="events" tabIndex={0} aria-label={label}>{[...run.events].reverse().map(e=><div key={e.id}><time>{stamp(e.at)}</time><span className="event-stage">{e.stage}</span><p>{e.message}</p>{e.detail?.seconds&&<small>{sec(e.detail.seconds)}</small>}</div>)}</div>:<div className="empty"><History size={24}/><span>{run?'No events yet':'No run selected'}</span></div>;
}
function PreparationAudit({run}){
  const [preference,setPreference]=useState(null);
  const expanded=runSectionExpanded(run,'audit',preference);
  return <div className="run-details preparation-audit"><div className="run-detail-heading"><h3><button type="button" id="preparation-heading-audit" className="disclosure-button" aria-expanded={expanded} aria-controls="preparation-panel-audit" onClick={()=>setPreference(!expanded)}><ChevronRight size={16}/><History size={15}/><span>Preparation audit</span></button></h3></div>{['failed','interrupted'].includes(run.status)&&<div className="health error" role="status"><AlertTriangle size={15}/>{run.error||'Preparation did not complete'}</div>}<div id="preparation-panel-audit" role="region" aria-labelledby="preparation-heading-audit" hidden={!expanded} className="run-detail-panel"><AuditEvents run={run} label="Preparation audit events"/></div></div>;
}
function RunDetails({run,inspect,correctDecision,editable,busy,historical=false}){
  const [preferences,setPreferences]=useState({});
  const decisions=run?.decisions||[],metrics=run?.metrics||{};
  const auditOpen=runSectionExpanded(run,'audit',preferences.audit),decisionsOpen=runSectionExpanded(run,'decisions',preferences.decisions);
  function toggle(section){setPreferences(current=>({...current,[section]:!runSectionExpanded(run,section,current[section])}));}
  return <div className="run-details">
    <div className="run-detail-heading"><h3><button id="run-heading-audit" type="button" className="disclosure-button" aria-expanded={auditOpen} aria-controls="run-panel-audit" onClick={()=>toggle('audit')}><ChevronRight size={16}/><History size={15}/><span>{historical?'Run Audit':'Live Audit'}</span></button></h3></div>
    <div id="run-panel-audit" role="region" aria-labelledby="run-heading-audit" hidden={!auditOpen} className="run-detail-panel audit-band">
      <AuditEvents run={run}/>
    </div>
    <div className="run-detail-heading"><h3><button id="run-heading-decisions" type="button" className="disclosure-button" aria-expanded={decisionsOpen} aria-controls="run-panel-decisions" onClick={()=>toggle('decisions')}><ChevronRight size={16}/><ShieldCheck size={15}/><span>Decisions</span><span className="detail-count">{decisions.length}</span></button></h3></div>
    <div id="run-panel-decisions" role="region" aria-labelledby="run-heading-decisions" hidden={!decisionsOpen} className="run-detail-panel decision-band">
      {decisions.length>0?<><div className="decision-summary-line">{metrics.confirmed??metrics.count??'--'} included / {metrics.decision_workflow?`${metrics.disagreements??0} reviewer disagreements`:`${metrics.uncertain??'--'} initially uncertain`} / {metrics.excluded??'--'} excluded / {metrics.known_investments??'--'} known investments{metrics.merged_selected_coverage?.length>0&&<> / {metrics.merged_selected_coverage.length} repeated financing records merged</>}{metrics.manual_corrections>0&&<> / {metrics.manual_corrections} user-corrected</>}</div><DecisionTable decisions={decisions} inspect={inspect} correctDecision={correctDecision} editable={editable&&['completed','local_completed'].includes(run?.status)} busy={busy} pending={metrics.pending_rechecks||[]}/></>:<div className="empty">No decisions yet</div>}
    </div>
  </div>;
}
function DecisionTable({decisions,inspect,correctDecision,editable,busy,pending}){
  const [filter,setFilter]=useState('all'),[editing,setEditing]=useState(null);
  const filters=[['all','All'],['disagreed','Disagreements']];
  const visible=decisions.filter(d=>decisionMatchesFilter(d,filter));
  return <>
    <div className="table-tools decision-tools"><label htmlFor="decision-filter">Show</label><select id="decision-filter" aria-label="Filter decisions" value={filter} onChange={e=>setFilter(e.target.value)}>{filters.map(([value,name])=><option key={value} value={value}>{name} ({decisions.filter(d=>decisionMatchesFilter(d,value)).length})</option>)}</select><span aria-live="polite">{visible.length} of {decisions.length} deals</span></div>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Decision records"><table><thead><tr><th>Company</th><th>Reviewer 1</th><th>Reviewer 2</th><th>Final decision</th><th>Decision basis</th><th>Reason</th><th/></tr></thead><tbody>{visible.map(d=><tr key={d.deal_id} onClick={inspect?()=>inspect(d.deal_id):undefined}><td><strong>{d.company}</strong></td><td><Badge value={d.first_pass.fit}/></td><td>{d.verification?<Badge value={d.verification.fit}/>:'Not reviewed'}</td><td>{d.final.known_investment?<span className="badge neutral">Portfolio</span>:<Badge value={d.final.fit}/>} {d.final.manual_override&&<small>Edited</small>}</td><td>{decisionResolution(d)}{decisionMatchesFilter(d,'disagreed')&&<small className="disagreement-label">Reviewers disagreed</small>}</td><td className="reason-cell">{d.final.reason}</td><td><div className="decision-actions">{editable&&!d.final.known_investment&&<button className="icon-btn" title={'Edit decision for '+d.company} aria-label={'Edit decision for '+d.company} disabled={busy} onClick={e=>{e.stopPropagation();setEditing(d)}}><Pencil size={15}/></button>}{inspect&&<ChevronRight size={16}/>}</div></td></tr>)}</tbody></table>{!visible.length&&<div className="empty">No decisions match this filter</div>}</div>
    {editing&&<DecisionEditor decision={editing} confirming={pending.includes(editing.deal_id)} close={()=>setEditing(null)} save={async(fit,reason,url)=>{await correctDecision(editing,fit,reason,url);setEditing(null)}}/>}
  </>;
}
function EditorFrame({title,close,saving,children}){
  const dialog=useRef(null);
  useEffect(()=>{const previous=document.activeElement;dialog.current.querySelector('select,input,textarea,button').focus();return()=>previous?.focus();},[]);
  function keyboard(event){
    if(event.key==='Escape'&&!saving){event.stopPropagation();close();}
    if(event.key==='Tab'){const items=Array.from(dialog.current.querySelectorAll('button:not(:disabled),select:not(:disabled),textarea:not(:disabled),input:not(:disabled),a[href]'));const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
  }
  return <div className="decision-editor-overlay" onClick={()=>{if(!saving)close()}}><div className="decision-editor" role="dialog" aria-modal="true" aria-labelledby="correction-editor-title" ref={dialog} onClick={e=>e.stopPropagation()} onKeyDown={keyboard}><header><h2 id="correction-editor-title">{title}</h2><button className="icon-btn" title="Close correction editor" aria-label="Close correction editor" disabled={saving} onClick={close}><X size={18}/></button></header>{children}</div></div>;
}
function DecisionEditor({decision,close,save,confirming=false}){
  const [fit,setFit]=useState(decision.final.fit),[reason,setReason]=useState(''),[url,setUrl]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('');
  async function submit(event){event.preventDefault();setSaving(true);setError('');try{await save(fit,reason.trim(),url.trim())}catch(e){setError(e.message);setSaving(false)}}
  return <EditorFrame title={'Change Decision: '+decision.company} close={close} saving={saving}><form onSubmit={submit}><label htmlFor="edited-fit">Final Fit</label><select id="edited-fit" value={fit} onChange={e=>setFit(e.target.value)} disabled={saving}>{['Yes','No'].map(value=><option key={value}>{value}</option>)}</select><label htmlFor="edited-reason">Reasoning (optional)</label><textarea id="edited-reason" value={reason} onChange={e=>setReason(e.target.value)} maxLength={3000} disabled={saving}/><label htmlFor="edited-decision-source">Decision source URL (optional)</label><input id="edited-decision-source" type="url" value={url} onChange={e=>setUrl(e.target.value)} maxLength={2000} disabled={saving}/>{error&&<div role="alert" className="error">{error}</div>}<footer><button className="secondary" type="button" disabled={saving} onClick={close}>Cancel</button><button className="primary" type="submit" disabled={saving||(!confirming&&fit===decision.final.fit&&!reason.trim()&&!url.trim())}><Save size={15}/>{saving?'Saving':'Save Decision'}</button></footer></form></EditorFrame>;
}
function fieldName(key){return ({growth_stage:'Growth or Early',deal_date:'Deal Date',website:'Website',round:'Round',amount:'Amount Raised',investors:'Investors',sector:'Sector',description:'Description',geography:'Geography',announcement_link:'Announcement Link'})[key]||key}
function evidenceName(e){if(e.source)return e.source+(e.date?' · '+e.date:'');if(e.url){try{const url=new URL(e.url);return url.hostname+url.pathname.replace(/\/$/,'')}catch{}}return e.kind==='human'?'User correction':'Supporting evidence'}
function EvidenceGroup({group,cited}){
  return <details id={'evidence-'+group.id} className={group.source?'':'web-evidence'}><summary>{group.source?<Mail size={15}/>:<FileText size={15}/>}<span>{evidenceName(group)}</span>{cited&&<small className="cited-label">Cited for decision</small>}</summary>{group.url&&<a href={group.url} target="_blank" rel="noreferrer">{group.url}<ExternalLink size={13}/></a>}{group.items.map((item,i)=><div key={i}>{item.text&&<p className="source-text">{item.text}</p>}{item.quote&&<blockquote>{item.quote}</blockquote>}{item.summary&&<p>{item.summary}</p>}{item.fields&&<small>{item.fields.map(fieldName).join(', ')}{item.at&&' / '+new Date(item.at).toLocaleString()}</small>}</div>)}</details>;
}
function DealDetail({deal,close,save,recheck,options,repairField,clearRepair,busy,pending}){
  const [editing,setEditing]=useState(false),[draft,setDraft]=useState({...deal.editable_fields});
  const [draftFit,setDraftFit]=useState(''),[reason,setReason]=useState(''),[decisionUrl,setDecisionUrl]=useState(''),[decisionEvidence,setDecisionEvidence]=useState('');
  const [note,setNote]=useState(''),[fieldUrl,setFieldUrl]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('');
  const [evidenceOpen,setEvidenceOpen]=useState(false),[selectedEvidence,setSelectedEvidence]=useState(null);
  const form=useRef(null);
  const decision=deal.review_decision||deal.decisions[0],editable=deal.editable&&!busy;
  const canEditDecision=deal.editable_decision??Boolean(deal.editable_run_id&&!decision?.final.known_investment);
  const sources=evidenceGroups([...deal.source_evidence,...deal.evidence.filter(e=>['web','human'].includes(e.kind))]);
  const cited=new Set([...(decision?.final.evidence_ids||[]),decision?.final.manual_override?.evidence_id].filter(Boolean));
  const changes=Object.fromEntries(Object.entries(draft).filter(([key,value])=>value!==deal.editable_fields[key]));
  const decisionChanged=canEditDecision&&['Yes','No'].includes(draftFit)&&(draftFit!==decision?.final.fit||reason.trim()||decisionUrl.trim()||decisionEvidence||pending);
  const changed=Object.keys(changes).length>0||decisionChanged;
  function begin(){setDraft({...deal.editable_fields});setDraftFit(decision?.final.fit||'');setReason('');setDecisionUrl('');setDecisionEvidence('');setNote('');setFieldUrl('');setError('');setEditing(true)}
  function cancel(){setEditing(false);setError('');clearRepair()}
  function revealEvidence(id){setEvidenceOpen(true);setSelectedEvidence(id)}
  useEffect(()=>{if(repairField&&deal.editable)begin()},[repairField]);
  useEffect(()=>{if(editing&&repairField){const input=document.getElementById('company-field-'+repairField);input?.focus();input?.scrollIntoView({block:'nearest'})}},[editing,repairField]);
  useEffect(()=>{if(evidenceOpen&&selectedEvidence){const target=document.getElementById('evidence-'+selectedEvidence);if(target){target.open=true;target.scrollIntoView({block:'nearest'})}setSelectedEvidence(null)}},[evidenceOpen,selectedEvidence]);
  async function submit(event){
    event.preventDefault();if(!changed||!form.current.reportValidity())return;
    setSaving(true);setError('');
    try{await save(deal,changes,decisionChanged?{fit:draftFit,reason:reason.trim(),source_url:decisionUrl.trim(),evidence_id:decisionEvidence}:null,note.trim(),fieldUrl.trim(),'');setEditing(false);clearRepair()}
    catch(error){setError(error.message)}finally{setSaving(false)}
  }
  function fieldInput(key){
    const props={id:'company-field-'+key,value:draft[key]||'',disabled:saving||busy,onChange:event=>setDraft(current=>({...current,[key]:event.target.value}))};
    if(options[key])return <select {...props}><option value="">Not available</option>{draft[key]&&!options[key].includes(draft[key])&&<option value={draft[key]}>{draft[key]}</option>}{options[key].map(value=><option key={value}>{value}</option>)}</select>;
    if(['description','investors'].includes(key))return <textarea {...props} rows={key==='description'?3:2} maxLength={5000}/>;
    return <input {...props} type={key==='deal_date'?'date':key==='website'||key==='announcement_link'?'url':'text'} maxLength={5000}/>;
  }
  return <div className="drawer-overlay" onClick={()=>{if(!saving&&!busy)close()}}><aside className={'drawer '+(editing?'editing':'')} onClick={event=>event.stopPropagation()}>
    <header><div><small>{deal.historical?'HISTORICAL FINANCING RECORD':'FINANCING RECORD'}</small><h2>{deal.company}</h2></div><div className="drawer-header-actions">
      {editing?<><button type="button" className="text-button" disabled={saving||busy} onClick={cancel}>Cancel</button><button className="primary" type="submit" form="company-inline-form" disabled={saving||busy||!changed}><Save size={15}/>{saving?'Saving':'Save'}</button></>:deal.editable&&<button type="button" className="text-button" disabled={!editable} onClick={begin}><Pencil size={15}/>Edit</button>}
      <button type="button" className="icon-btn" title="Close company details" aria-label="Close company details" disabled={saving||busy} onClick={close}><X size={20}/></button>
    </div></header>
    <form id="company-inline-form" ref={form} className="drawer-body" onSubmit={submit}>
      {error&&<div className="notice error" role="alert">{error}</div>}
      <section><h3>Review Decision</h3>
        {editing&&canEditDecision?<div className="inline-decision-fields">
          <label htmlFor="company-fit">Fit</label><select id="company-fit" value={draftFit} disabled={saving||busy} onChange={event=>setDraftFit(event.target.value)}><option value="" disabled>Unreviewed</option><option>Yes</option><option>No</option></select>
          <label htmlFor="company-decision-reason">Reasoning <small>(optional)</small></label><textarea id="company-decision-reason" rows={2} value={reason} maxLength={3000} disabled={saving||busy} onChange={event=>setReason(event.target.value)} placeholder="Add your reasoning"/>
          <label htmlFor="company-decision-evidence">Decision source <small>(optional)</small></label><select id="company-decision-evidence" value={decisionEvidence} disabled={saving||busy} onChange={event=>setDecisionEvidence(event.target.value)}><option value="">Choose existing evidence</option>{sources.map(e=><option key={e.id} value={e.id}>{evidenceName(e)}</option>)}</select>
          <label htmlFor="company-decision-url">Decision source URL <small>(optional)</small></label><input id="company-decision-url" type="url" value={decisionUrl} maxLength={2000} disabled={saving||busy} onChange={event=>setDecisionUrl(event.target.value)} placeholder="https://"/>
        </div>:decision?<>
          <div className="decision-summary"><Badge value={decision.final.fit}/><span>{decision.final.manual_override?'User-corrected decision':Number.isFinite(decision.final.confidence)?Math.round(decision.final.confidence*100)+'% model confidence':''}</span>{decision.verification&&<ShieldCheck size={17}/>}</div>
          <ReviewComparison decision={decision}/><div className="decision-reasoning"><small>Final Decision Reasoning</small><p>{decision.final.reason}</p></div>
          <div className="decision-citations"><small>Decision Sources</small>{sources.filter(e=>e.ids.some(id=>cited.has(id))).map(e=><a key={e.id} href={'#evidence-'+e.id} onClick={event=>{event.preventDefault();revealEvidence(e.id)}}>{evidenceName(e)}<ChevronRight size={12}/></a>)}{decision.final.manual_override?.source_url&&<a href={decision.final.manual_override.source_url} target="_blank" rel="noreferrer">{decision.final.manual_override.source_url}<ExternalLink size={12}/></a>}</div>
        </>:<small>Unreviewed</small>}
        {pending&&<div className="recheck-pending"><small>Corrected facts may affect this decision.</small><button type="button" className="secondary" disabled={!editable||editing} onClick={()=>recheck(deal)}><ShieldCheck size={15}/>Recheck Company</button></div>}
        {decision?.final.latest_recheck&&<div className="decision-reasoning"><small>Latest recheck: {decision.final.latest_recheck.fit}.{decision.final.manual_override&&' Your explicit decision remains in place.'}</small><p>{decision.final.latest_recheck.reason}</p></div>}
      </section>
      <section><h3>Company & Financing</h3><dl>{Object.entries(deal.fields).map(([key,value])=><React.Fragment key={key}>
        <dt>{editing?<label htmlFor={'company-field-'+key}>{fieldName(key)}</label>:fieldName(key)}</dt><dd>{editing?fieldInput(key):value?<>{key==='website'||key==='announcement_link'?<a href={value} target="_blank" rel="noreferrer">{value}<ExternalLink size={12}/></a>:value}</>:<span className="missing">{deal.missing[key]||'Not available'}</span>}</dd>
      </React.Fragment>)}</dl>
        {editing&&<div className="inline-field-note"><label htmlFor="company-field-note">Change note <small>(optional)</small></label><textarea id="company-field-note" rows={2} value={note} maxLength={3000} disabled={saving||busy} onChange={event=>setNote(event.target.value)}/><label htmlFor="company-field-source">Source URL <small>(optional)</small></label><input id="company-field-source" type="url" value={fieldUrl} maxLength={2000} disabled={saving||busy} onChange={event=>setFieldUrl(event.target.value)} placeholder="https://"/></div>}
      </section>
      <section className="company-evidence"><h3><button type="button" className="disclosure-button" aria-expanded={evidenceOpen} aria-controls="company-source-evidence" onClick={()=>setEvidenceOpen(!evidenceOpen)}><span>Source Evidence</span><ChevronRight size={16}/></button></h3><div id="company-source-evidence" hidden={!evidenceOpen}>{sources.map(e=><EvidenceGroup key={e.id} group={e} cited={e.ids.some(id=>cited.has(id))}/>)}</div></section>
      <section hidden={deal.historical}><h3>Correction History</h3>{!deal.field_history.length&&!deal.feedback.length&&<small>No user corrections</small>}
        {deal.field_history.map((f,i)=><div className="feedback" key={'field-'+i}><strong>{fieldName(f.field)}</strong><p>{f.old_value||'Missing'} → {f.value||'Missing'}</p><p>{f.reason}</p>{f.source_url&&<a href={f.source_url} target="_blank" rel="noreferrer">{f.source_url}</a>}<small>{new Date(f.at).toLocaleString()} / {f.active?'Current':'Superseded'}</small></div>)}
        {deal.feedback.map(f=><div className="feedback" key={f.id}><strong>Review Decision</strong><p>{(f.previous?JSON.parse(f.previous):null)?.fit||'Unreviewed'} → {f.fit}</p><p>{f.reason}</p>{f.source_url&&<a href={f.source_url} target="_blank" rel="noreferrer">{f.source_url}</a>}{f.evidence_id&&<a href={'#evidence-'+f.evidence_id} onClick={event=>{event.preventDefault();const group=sources.find(e=>e.ids.includes(f.evidence_id));if(group)revealEvidence(group.id)}}>Decision source<ChevronRight size={12}/></a>}<small>{new Date(f.at).toLocaleString()} / {f.active?'Current':'Superseded'}</small></div>)}
      </section>
    </form>
  </aside></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
