export function selectedRunWeek(overview, currentWeek) {
  if (overview.active_run) return overview.active_run.week_id;
  if (overview.weeks.some(week => week.id === currentWeek)) return currentWeek;
  return overview.weeks.find(week => week.status === 'prepared')?.id || overview.weeks[0]?.id || '';
}

export function resultMessage(run) {
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
