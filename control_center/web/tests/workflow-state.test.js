import assert from 'node:assert/strict';
import {test} from 'node:test';
import {decisionMatchesFilter, resultMessage, selectedRunWeek, sourceStatus} from '../src/workflow-state.js';

test('the selected week follows an active background workflow', () => {
  assert.equal(selectedRunWeek({active_run:{week_id:'older-week'},weeks:[{id:'latest',status:'prepared'},{id:'older-week',status:'raw'}]}, 'latest'), 'older-week');
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
