import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync(new URL('../app/AppClient.js', import.meta.url),'utf8');

test('batch reanalysis is connected to the global progress queue',()=>{
  assert.match(source,/job_type:\s*"reanalysis"/);
  assert.match(source,/setAnalysisModalOpen\(true\)/);
  assert.match(source,/재분석":"분석"/);
  assert.match(source,/BATCH REANALYSIS/);
});

test('data management exposes per-batch live reanalysis status',()=>{
  assert.match(source,/재분석 \{Math\.round\(job\.progress\|\|0\)\}%/);
  assert.match(source,/진행 상태/);
  assert.match(source,/analysisQueue=\{analysisQueue\}/);
});
