import assert from 'node:assert/strict';
import {test} from 'node:test';
import {canEditReview, datasetCounts, decisionMatchesFilter, evidenceGroups, preparationForWeek, resultMessage, runForWeek, runSectionExpanded, runsForWeek, selectedRunWeek, sourceStatus, stageDuration} from '../src/workflow-state.js';

test('evidence groups repeated pages and preserves every citation identifier', () => {
  const sources=[{id:'first',url:'https://example.com/news',quote:'A funding round'},{id:'second',url:'https://example.com/news/',quote:'A funding round'},{id:'third',url:'https://example.com/news#details',quote:'Another supporting excerpt'}];
  const groups=evidenceGroups(sources);
  assert.equal(groups.length,1);
  assert.deepEqual(groups[0].ids,['first','second','third']);
  assert.equal(groups[0].items.length,2);
  assert.equal(sources[0].ids,undefined);
});

test('newsletter grouping distinguishes different emails on the same date', () => {
  const groups=evidenceGroups([{id:'a',source:'Newsletter',message_id:'one',date:'today',text:'Deal excerpt'},{id:'b',source:'Newsletter',message_id:'one',date:'today',text:'Extra deal excerpt'},{id:'c',source:'Newsletter',message_id:'two',date:'today',text:'Different edition'}]);
  assert.equal(groups.length,2);
  assert.deepEqual(groups[0].ids,['a','b']);
});

test('only the latest completed review for current prepared inputs is editable', () => {
  const week={id:'week',updated_at:'current'};
  const run={id:'review',kind:'review',week_id:'week',status:'completed',metrics:{prepared_at:'current'}};
  assert.equal(canEditReview(null,[],week),false);
  assert.equal(canEditReview(run,[],week),false);
  assert.equal(canEditReview(run,[run],week),true);
  assert.equal(canEditReview({...run,status:'running'},[run],week),false);
  assert.equal(canEditReview(run,[{...run,id:'newer'},run],week),false);
  assert.equal(canEditReview(run,[run],{...week,updated_at:'new-inputs'}),false);
});

test('history filters by the selected week without changing run order or records', () => {
  const runs=[{id:'new',week_id:'week-a',status:'failed'},{id:'other',week_id:'week-b'},{id:'old',kind:'review',week_id:'week-a',status:'completed'}];
  const before=JSON.stringify(runs);
  assert.deepEqual(runsForWeek(runs,'week-a').map(run=>run.id),['new','old']);
  assert.deepEqual(runsForWeek(runs,'week-b').map(run=>run.id),['other']);
  assert.deepEqual(runsForWeek(runs,'week-c'),[]);
  assert.deepEqual(runsForWeek([], 'week-a'),[]);
  assert.equal(JSON.stringify(runs),before);
});

test('a fresh view follows an active background workflow without overriding week navigation', () => {
  const overview={active_run:{week_id:'older-week'},weeks:[{id:'latest',status:'prepared'},{id:'older-week',status:'raw'}]};
  assert.equal(selectedRunWeek(overview, ''), 'older-week');
  assert.equal(selectedRunWeek(overview, 'latest'), 'latest');
});

test('background progress stays associated with its week while browsing other weeks', () => {
  const background={id:'active',kind:'review',week_id:'week-a',status:'running'};
  const historical={id:'old',kind:'review',week_id:'week-b',status:'completed'};
  assert.equal(runForWeek(null,background,'week-a'),background);
  assert.equal(runForWeek(null,background,'week-b'),null);
  assert.equal(runForWeek(historical,background,'week-b'),historical);
  assert.equal(runForWeek(historical,background,'week-a'),background);
  assert.equal(background.status,'running');
});

test('finished background results remain available without replacing an opened historical run', () => {
  const background={id:'finished',kind:'review',week_id:'week-a',status:'completed'};
  const historical={id:'old',kind:'review',week_id:'week-a',status:'completed'};
  assert.equal(runForWeek(null,background,'week-a'),background);
  assert.equal(runForWeek(historical,background,'week-a'),historical);
  assert.equal(runForWeek(null,null,'week-a'),null);
});

test('an idle view preserves a valid selected week', () => {
  assert.equal(selectedRunWeek({weeks:[{id:'latest',status:'prepared'},{id:'older',status:'prepared'}]}, 'older'), 'older');
});

test('a fresh view prefers prepared data, then raw data', () => {
  assert.equal(selectedRunWeek({weeks:[{id:'raw',status:'raw'},{id:'ready',status:'prepared'}]}, ''), 'ready');
  assert.equal(selectedRunWeek({weeks:[{id:'raw',status:'raw'}]}, ''), 'raw');
  assert.equal(selectedRunWeek({weeks:[]}, ''), '');
});

test('preparation never claims Google publication', () => {
  for (const kind of ['prepare','enrich','reconcile','rates']) {
    assert.equal(resultMessage({kind,status:'completed'}), 'Data prepared; no Google writes');
  }
  assert.equal(resultMessage({kind:'review',status:'completed'}), 'Published and confirmed');
  assert.equal(resultMessage({kind:'review',status:'local_completed'}), 'Local review finished; no Google writes');
});

test('saved corrections never claim that stale outputs have been updated', () => {
  assert.equal(resultMessage({kind:'review',status:'completed',metrics:{outputs_dirty:true}}),'Corrections saved; outputs need refresh');
  assert.equal(resultMessage({kind:'review',status:'local_completed',metrics:{manual_corrections:1}}),'Local review corrected; no Google writes');
});

test('failed and interrupted runs retain the recorded failure', () => {
  assert.equal(resultMessage({kind:'review',status:'failed',error:'Source evidence failed'}), 'Source evidence failed');
  assert.equal(resultMessage({kind:'prepare',status:'interrupted'}), 'Run did not complete');
});

test('confirmed newsletter absence is neutral, not a coverage warning', () => {
  assert.equal(sourceStatus({emails:5}).state, 'captured');
  assert.equal(sourceStatus(undefined, {status:'not_published',reason:'User confirmed none arrived'}).state, 'not_published');
  assert.equal(sourceStatus({emails:0}).state, 'unknown');
});

test('uncertain decisions use the first-pass Maybe or below-90-percent threshold', () => {
  assert.equal(decisionMatchesFilter({first_pass:{fit:'Maybe',confidence:.99}}, 'uncertain'), true);
  assert.equal(decisionMatchesFilter({first_pass:{fit:'Yes',confidence:.89}}, 'uncertain'), true);
  assert.equal(decisionMatchesFilter({first_pass:{fit:'No',confidence:.9}}, 'uncertain'), false);
  assert.equal(decisionMatchesFilter({first_pass:{fit:'No',confidence:1}}, 'uncertain'), false);
  assert.equal(decisionMatchesFilter({first_pass:{fit:'Yes'}}, 'uncertain'), false);
});

test('disagreement compares fit and portfolio status from both checks', () => {
  const first_pass={fit:'No',confidence:.95,known_investment:false};
  assert.equal(decisionMatchesFilter({first_pass,verification:{...first_pass,fit:'Yes'}}, 'disagreed'), true);
  assert.equal(decisionMatchesFilter({first_pass,verification:{...first_pass,known_investment:true}}, 'disagreed'), true);
  assert.equal(decisionMatchesFilter({first_pass,verification:{...first_pass,confidence:.8,reason:'New explanation'}}, 'disagreed'), false);
  assert.equal(decisionMatchesFilter({first_pass,final:{fit:'Yes'}}, 'disagreed'), false);
  assert.equal(decisionMatchesFilter({first_pass,verification:null}, 'disagreed'), false);
});

test('decision filters return the matching records without mutation', () => {
  const decisions=[
    {id:'both',first_pass:{fit:'Maybe',confidence:.8},verification:{fit:'Yes'}},
    {id:'uncertain',first_pass:{fit:'Yes',confidence:.8},verification:{fit:'Yes'}},
    {id:'disagreed',first_pass:{fit:'No',confidence:.99},verification:{fit:'Yes'}},
    {id:'neither',first_pass:{fit:'Yes',confidence:.99}}
  ];
  const before=JSON.stringify(decisions);
  assert.deepEqual(decisions.filter(d=>decisionMatchesFilter(d,'uncertain')).map(d=>d.id), ['both','uncertain']);
  assert.deepEqual(decisions.filter(d=>decisionMatchesFilter(d,'disagreed')).map(d=>d.id), ['both','disagreed']);
  assert.equal(decisions.filter(d=>decisionMatchesFilter(d,'all')).length,4);
  assert.equal(JSON.stringify(decisions),before);
});

test('sub-second stage timings display 0.1s without changing exact values', () => {
  for (const seconds of [0,.003626,.05,.9,.999]) assert.equal(stageDuration(seconds),'0.1s');
  assert.equal(stageDuration(1),'1.0s');
  assert.equal(stageDuration(40.74),'40.7s');
  assert.equal(stageDuration(null),'--');
  assert.equal(stageDuration(undefined),'--');
});

test('audit expands during execution and failures, then collapses on success', () => {
  for (const status of ['running','failed','interrupted']) assert.equal(runSectionExpanded({status},'audit'),true);
  for (const status of ['completed','local_completed']) assert.equal(runSectionExpanded({status},'audit'),false);
  assert.equal(runSectionExpanded(null,'audit'),false);
});

test('manual disclosure choices are preserved through status changes', () => {
  for (const section of ['audit','decisions']) {
    for (const status of ['running','completed','failed']) {
      assert.equal(runSectionExpanded({status},section,true),true);
      assert.equal(runSectionExpanded({status},section,false),false);
    }
  }
});

test('decisions stay collapsed by default independently of audit', () => {
  for (const status of ['running','completed','failed']) assert.equal(runSectionExpanded({status},'decisions'),false);
  assert.equal(runSectionExpanded(null,'decisions'),false);
  assert.equal(runSectionExpanded({status:'running'},'audit'),true);
});


test('preparation cannot replace a saved week review or become a review result', () => {
  const saved={id:'saved',kind:'review',week_id:'a',status:'completed',decisions:[{id:'decision'}],events:[{id:'audit'}]};
  for(const status of ['running','completed','failed']){
    const preparation={id:'prep',kind:'prepare',week_id:'a',status};
    assert.equal(runForWeek(null,preparation,'a',saved),saved);
    assert.equal(runForWeek(preparation,preparation,'a',saved),saved);
    assert.equal(runForWeek(null,preparation,'b',saved),null);
    assert.equal(runForWeek(null,preparation,'a',null),null);
  }
});

test('active reviews show progress while week switches retain the applicable saved review', () => {
  const active={id:'new',kind:'review',week_id:'a',status:'running'};
  const savedA={id:'old-a',kind:'review',week_id:'a',status:'completed'};
  const savedB={id:'old-b',kind:'review',week_id:'b',status:'local_completed'};
  assert.equal(runForWeek(null,active,'a',savedA),active);
  assert.equal(runForWeek(null,active,'b',savedB),savedB);
  assert.equal(runForWeek(null,active,'b',savedA),null);
  assert.equal(runForWeek(savedA,active,'a',savedA),savedA);
});

test('both final dataset counts remain masked through preparation and overview readback', () => {
  const old={id:'a',status:'prepared',report:{duplicate_mentions:10},remaining_missing_count:5};
  const running={id:'prep',kind:'prepare',week_id:'a',status:'running'};
  assert.equal(preparationForWeek(running,null,'a'),true);
  assert.equal(preparationForWeek(running,null,'b'),false);
  assert.equal(preparationForWeek({...running,kind:'review'},null,'a'),false);
  assert.deepEqual(datasetCounts(old,true),{duplicates:'Preparing',missing:'Preparing',available:false});
  const complete={...running,status:'completed'};
  assert.equal(preparationForWeek(complete,{week_id:'a',run_id:'prep'},'a'),true);
  assert.deepEqual(datasetCounts(old,preparationForWeek(complete,{week_id:'a'},'a')),{duplicates:'Preparing',missing:'Preparing',available:false});
  const fresh={...old,report:{duplicate_mentions:17},remaining_missing_count:0};
  assert.deepEqual(datasetCounts(fresh,preparationForWeek(complete,null,'a')),{duplicates:17,missing:0,available:true});
  assert.deepEqual(datasetCounts({...fresh,status:'raw'},false),{duplicates:'--',missing:'--',available:false});
});
