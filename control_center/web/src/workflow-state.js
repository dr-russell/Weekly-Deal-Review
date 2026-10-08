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

export function preparationForWeek(activeRun, pending, week) {
  return Boolean((activeRun?.status === 'running' && activeRun.kind !== 'review' && activeRun.week_id === week) || pending?.week_id === week);
}

export function datasetCounts(week, preparing) {
  if (preparing) return {duplicates:'Preparing',missing:'Preparing',available:false};
  if (week?.status !== 'prepared') return {duplicates:'--',missing:'--',available:false};
  return {duplicates:week.report?.duplicate_mentions??'--',missing:week.remaining_missing_count??'--',available:true};
}

export function runsForWeek(runs, week) {
  return runs.filter(run => run.week_id === week);
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

export function stageDuration(seconds) {
  if (seconds == null) return '--';
  return (seconds < 1 ? .1 : seconds).toFixed(1) + 's';
}

export function runSectionExpanded(run, section, preference=null) {
  if (typeof preference === 'boolean') return preference;
  return section === 'audit' && ['running','failed','interrupted'].includes(run?.status);
}
