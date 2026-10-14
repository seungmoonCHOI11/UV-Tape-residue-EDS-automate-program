import test from 'node:test';
import assert from 'node:assert/strict';
import {criterionResult,criterionScore,finalClass,pointRatios} from '../app/screeningModel.mjs';

test('C AND N AND O requires all three strong gates',()=>{
  assert.equal(criterionResult({c:2.5,n:2.5,o:3.2},'cno',2.4),'Residue');
  assert.equal(criterionResult({c:2.5,n:2.2,o:3.2},'cno',2.4),'Ambiguous');
  assert.equal(criterionResult({c:2.5,n:1.8,o:3.2},'cno',2.4),'Non-residue');
});

test('C AND N requires both C and N strong gates',()=>{
  assert.equal(criterionResult({c:2.5,n:2.5,o:1.5},'cn',2.4),'Residue');
  assert.equal(criterionResult({c:2.5,n:2.2,o:4.0},'cn',2.4),'Ambiguous');
  assert.equal(criterionResult({c:1.8,n:3.0,o:4.0},'cn',2.4),'Non-residue');
});

test('criterion score follows logical bottleneck rather than averaging',()=>{
  const a=criterionScore({c:2.5,n:2.1,o:3.5},'cno',2.4);
  const b=criterionScore({c:2.5,n:2.5,o:1.5},'cn',2.4);
  assert.ok(a < 70);
  assert.ok(b >= 70);
});

test('human verification stays final while automatic criterion changes',()=>{
  const p={human_result:'Non-residue',features:{c_roi_global_ratio:3,n_roi_global_ratio:3,o_roi_global_ratio:4}};
  assert.equal(finalClass(p,'cno',2.4),'Non-residue');
});

test('legacy N log2 ratio remains usable without reanalysis',()=>{
  const p={features:{c_roi_global_ratio:3,o_roi_global_ratio:4,n_log2_ratio:Math.log2(2.5)}};
  assert.ok(Math.abs(pointRatios(p).n-2.5)<1e-9);
  assert.equal(criterionResult(pointRatios(p),'cno',2.4),'Residue');
});

test('C and O strong thresholds are adjustable together with N',()=>{
  const r={c:2.55,n:2.55,o:2.85};
  assert.equal(criterionResult(r,'co',2.4,2.4,3.0),'Ambiguous');
  assert.equal(criterionResult(r,'co',2.4,2.5,2.8),'Residue');
  assert.equal(criterionResult(r,'cno',2.5,2.5,2.8),'Residue');
  assert.ok(criterionScore(r,'cno',2.5,2.5,2.8)>=70);
});
