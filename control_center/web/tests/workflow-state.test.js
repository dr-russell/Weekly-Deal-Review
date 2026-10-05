import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resultMessage, selectedRunWeek, sourceStatus} from '../src/workflow-state.js';

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
