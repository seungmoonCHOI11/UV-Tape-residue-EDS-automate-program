import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRuleStrata,comparisonLabel,ruleComparisonCsv} from '../app/ruleComparisonModel.mjs';
const filters={scope:'main',wafers:null};
const points=Array.from({length:9},(_,i)=>({id:String(i),power:'350W',time:'30s',wafer:4,point:i+1,substrate_type:'SiCN',human_result:i<4?'Ambiguous':i===4?'Non-residue':null,features:{c_roi_global_ratio:i<2?2.2:3.2,o_roi_global_ratio:4,n_roi_global_ratio:1}}));
test('same cohort gives four Human-first ambiguous points and two automatic ambiguous points',()=>{
 const auto=buildRuleStrata(points,filters)[0].conditions[0];
 const final=buildRuleStrata(points,filters,2.4,2.4,3,'final')[0].conditions[0];
 assert.equal(auto.rules.co.ambiguous,2);assert.equal(final.rules.co.ambiguous,4);
 assert.equal(final.rules.co.non,1);assert.equal(final.rules.co.residue,4);
 assert.equal(final.cells[1].rules.co.verified,5);
 assert.equal(auto.rules.co.n,final.rules.co.n);
 assert.equal(auto.rules.co.residue,7);
 assert.deepEqual(final.rules.cn,auto.rules.cn);
});
test('saved labels stay fixed across thresholds, unverified points use C-O fallback',()=>{
 assert.equal(comparisonLabel(points[2],'co','final',2.4,8,8),'Ambiguous');
 assert.equal(comparisonLabel(points[8],'co','final'),'Residue');
 assert.equal(comparisonLabel(points[8],'co','final',2.4,8,8),'Ambiguous');
 assert.equal(comparisonLabel(points[2],'co','auto'),'Residue');
 assert.equal(points[2].human_result,'Ambiguous');
});
test('CSV includes explicit final C-O labels and source alongside auto labels',()=>{
 const csv=ruleComparisonCsv(points);
 assert(csv.includes('Final C-O (Human first)'));assert(csv.includes('Auto C·O'));
 assert(csv.includes('Human'));assert(csv.includes('Auto C-O'));
});
