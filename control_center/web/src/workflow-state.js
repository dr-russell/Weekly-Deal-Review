export function selectedRunWeek(overview, currentWeek) {
  if (overview.weeks.some(week => week.id === currentWeek)) return currentWeek;
  if (overview.active_run) return overview.active_run.week_id;
  return overview.weeks.find(week => week.status === 'prepared')?.id || overview.weeks[0]?.id || '';
}

export function runForWeek(selectedRun, backgroundRun, week, savedReview=null) {
  if (selectedRun?.kind === 'review' && selectedRun.week_id === week) return selectedRun;
  if (backgroundRun?.kind === 'review' && backgroundRun.week_id === week && backgroundRun.status === 'running') return backgroundRun;
  if (savedReview?.kind === 'review' && savedReview.week_id === week) return savedReview;
  if (backgroundRun?.kind === 'review' && backgroundRun.week_id === week && ['completed','local_completed'].includes(backgroundRun.status)) return backgroundRun;
  return null;
}

export function runMatchesSelection(run, weeks) {
  return Boolean(run&&weeks.length&&(run.metrics?.week_ids||[run.week_id]).slice().sort().join('|')===weeks.slice().sort().join('|'));
}

export function runForSelection(selectedRun, backgroundRun, weeks, savedReview=null) {
  if(selectedRun?.kind==='review'&&runMatchesSelection(selectedRun,weeks))return selectedRun;
  if(backgroundRun?.kind==='review'&&backgroundRun.status==='running'&&runMatchesSelection(backgroundRun,weeks))return backgroundRun;
  if(savedReview?.kind==='review'&&runMatchesSelection(savedReview,weeks))return savedReview;
  return null;
}

export function canEditSelectionReview(run,runs,weeks,ids) {
  if(ids.length===1)return canEditReview(run,runs.filter(item=>runMatchesSelection(item,ids)),weeks.find(week=>week.id===ids[0]));
  if(!['completed','local_completed'].includes(run?.status)||!runMatchesSelection(run,ids))return false;
  const latest=runs.find(item=>item.kind==='review'&&['completed','local_completed'].includes(item.status)&&runMatchesSelection(item,ids));
  return Boolean(latest?.id===run.id&&run.metrics?.prepared_versions&&ids.every(id=>{const week=weeks.find(week=>week.id===id);return week?.status==='prepared'&&run.metrics.prepared_versions[id]===week.updated_at}));
}

export function preparationForWeek(activeRun, pending, week) {
  return Boolean((activeRun?.status === 'running' && activeRun.kind !== 'review' && activeRun.week_id === week) || pending?.week_id === week);
}

export function preparationForWeeks(activeRun, pending, weeks) {
  const overlaps=ids=>ids.some(id=>weeks.includes(id));
  return Boolean((activeRun?.status==='running'&&activeRun.kind!=='review'&&overlaps(activeRun.metrics?.week_ids||[activeRun.week_id]))||(pending&&overlaps(pending.week_ids||[pending.week_id])));
}

export function datasetCounts(week, preparing) {
  if (preparing) return {duplicates:'Preparing',missing:'Preparing',available:false};
  if (week?.status !== 'prepared') return {duplicates:'--',missing:'--',available:false};
  return {duplicates:week.report?.duplicate_mentions??'--',missing:week.remaining_missing_count??'--',available:true};
}

export function runsForWeek(runs, week) {
  return runs.filter(run => run.week_id === week || run.metrics?.week_ids?.includes(week));
}

export function canEditReview(run, runs, week) {
  if (!run || !week || !['completed','local_completed'].includes(run.status)) return false;
  const latest = runs.find(item => item.week_id === week.id && item.kind === 'review' && ['completed','local_completed'].includes(item.status));
  return Boolean(latest && run.id === latest.id && (!run.metrics?.prepared_at || run.metrics.prepared_at === week.updated_at));
}

export function evidenceGroups(evidence) {
  const groups = new Map();
  for (const item of evidence) {
    const key = item.source ? 'email:'+(item.message_id || item.id) : item.url ? 'url:'+item.url.replace(/#.*$/,'').replace(/\/$/,'') : 'id:'+item.id;
    if (!groups.has(key)) groups.set(key,{...item,ids:[],items:[]});
    const group = groups.get(key);
    group.ids.push(item.id);
    if (!group.items.some(existing=>existing.text===item.text&&existing.quote===item.quote&&existing.summary===item.summary)) group.items.push(item);
  }
  return [...groups.values()];
}

export function resultMessage(run) {
  if (run.metrics?.outputs_dirty) return 'Corrections saved; outputs need refresh';
  if (run.status === 'local_completed' && run.metrics?.manual_corrections) return 'Local review corrected; no Google writes';
  if (run.status === 'failed' || run.status === 'interrupted') return run.error || 'Run did not complete';
  if (run.status === 'local_completed') return 'Local review finished; no Google writes';
  if (run.kind === 'usd_refresh' && run.status === 'completed') return 'USD amounts published and confirmed';
  if (run.kind !== 'review' && run.status === 'completed') return 'Data prepared; no Google writes';
  if (run.kind === 'review' && run.status === 'completed') return 'Published and confirmed';
  return run.status;
}

export function sourceStatus(info, availability) {
  if (info?.emails > 0) return {state:'captured', reason:'Newsletter inputs captured'};
  if (availability?.status === 'not_published') return {state:'not_published', reason:availability.reason || 'No newsletters this week, confirmed'};
  return {state:'unknown', reason:'No inputs captured; newsletter availability has not been confirmed'};
}

export function decisionMatchesFilter(decision, filter) {
  const first = decision.first_pass;
  const independent = decision.verification;
  const uncertain = first?.fit === 'Maybe' || (Number.isFinite(first?.confidence) && first.confidence < .9);
  const disagreed = Boolean(first && independent &&
    (first.fit !== independent.fit || Boolean(first.known_investment) !== Boolean(independent.known_investment)));
  if (filter === 'uncertain') return uncertain;
  if (filter === 'disagreed') return disagreed;
  return true;
}

export function decisionResolution(decision) {
  if (decision.final?.manual_override) return 'User corrected';
  const result=decision.final?.review_resolution?.result;
  return ({supported_agreement:'Agreement',adjudicated:'Adjudicated',researched_and_adjudicated:'Research + adjudication'})[result] || 'Previous workflow';
}

export function workflowProgress(run) {
  const metrics=run?.metrics||{},stage=run?.stage;
  const finished=['completed','local_completed'].includes(run?.status);
  return [
    {key:'dual_review',name:'Independent reviews',done:finished||Boolean(metrics.dual_review_complete),running:stage==='dual_review'},
    {key:'adjudication',name:'Adjudicate',done:finished||Boolean(metrics.final_resolution_complete),running:['comparison','adjudication','research','final_resolution'].includes(stage)},
    {key:'validation',name:'Validate Rows',done:finished||['publication','confirmation','complete'].includes(stage),running:stage==='validation'},
    {key:'publication',name:'Publish',done:run?.status==='completed'||stage==='confirmation',running:stage==='publication',skipped:run?.status==='local_completed'},
    {key:'confirmation',name:'Confirm',done:run?.status==='completed',running:stage==='confirmation',skipped:run?.status==='local_completed'},
  ].map(step=>({...step,running:run?.status==='running'&&!step.done&&step.running}));
}

export function workflowCounts(run) {
  const metrics=run?.metrics||{};
  const finished=['completed','local_completed'].includes(run?.status);
  const total=metrics.companies_reviewed??0;
  const adjudicated=metrics.adjudicated??0;
  const researched=metrics.researched??0;
  // A deal is reviewed only when both original answers exist for that deal.
  const reviewed=metrics.dual_review_complete?total:metrics.reviewed_deals??0;
  // Older recorded runs counted initial assessments as ready, including cases awaiting research.
  const resolved=finished||metrics.final_resolution_complete?adjudicated:
    metrics.adjudication_resolved??((metrics.adjudication_complete||metrics.adjudication_assessment_complete)?
      Math.max(0,(metrics.adjudication_ready??0)-researched+(metrics.final_resolution_ready??0)):0);
  return {reviewed,total,resolved,adjudicated,researchReady:metrics.research_ready??0,researched};
}

export function workflowDuration(timings,key) {
  if(key!=='adjudication')return timings[key];
  const phases=['comparison','adjudication','research','final_resolution'].map(name=>timings[name]).filter(Number.isFinite);
  return phases.length?phases.reduce((total,value)=>total+value,0):null;
}

export function stageDuration(seconds) {
  if (seconds == null) return '--';
  return (seconds < 1 ? .1 : seconds).toFixed(1) + 's';
}

export function runSectionExpanded(run, section, preference=null) {
  if (typeof preference === 'boolean') return preference;
  return section === 'audit' && ['running','failed','interrupted'].includes(run?.status);
}
