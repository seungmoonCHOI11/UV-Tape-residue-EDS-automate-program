import test from 'node:test';
import assert from 'node:assert/strict';
import {autoRuleStats,ruleDistributions,ruleTransitions,buildRuleStrata} from '../app/ruleComparisonModel.mjs';

const pts=[
 {id:'a',power:'150W',time:'30s',wafer:1,point:1,substrate_type:'Si',human_result:'Residue',features:{c_roi_global_ratio:3.0,n_roi_global_ratio:1.5,o_roi_global_ratio:3.5}},
 {id:'b',power:'150W',time:'30s',wafer:4,point:1,substrate_type:'Si',human_result:'Non-residue',features:{c_roi_global_ratio:3.0,n_roi_global_ratio:2.8,o_roi_global_ratio:2.2}},
 {id:'c',power:'150W',time:'30s',wafer:5,point:1,substrate_type:'Si',features:{c_roi_global_ratio:2.2,n_roi_global_ratio:2.8,o_roi_global_ratio:3.5}},
];

test('three automatic rules are computed independently of Human labels',()=>{
 const co=autoRuleStats(pts,'co',2.4),cno=autoRuleStats(pts,'cno',2.4),cor=autoRuleStats(pts,'c_n_or_o',2.4);
 assert.equal(co.residue,1);
 assert.equal(cno.residue,0);
 assert.equal(cor.residue,2);
 assert.equal(co.verified,2);
});

test('rule distribution returns all three criteria together',()=>{
 const d=ruleDistributions(pts,2.4);
 assert.deepEqual(d.map(x=>x.rule.id),['co','cno','c_n_or_o']);
 assert.notDeepEqual(d.map(x=>x.residue),[1,1,1]);
});

test('condition-location matrix preserves all rules in same cells',()=>{
 const s=buildRuleStrata(pts,{scope:'main',wafers:null},2.4)[0];
 assert.equal(s.conditions.length,1);
 assert.equal(s.conditions[0].cells.length,3);
 assert.equal(s.conditions[0].cells[1].rules.c_n_or_o.residue,1);
 assert.equal(s.conditions[0].cells[1].rules.co.residue,0);
});

test('transitions are measured against C-O without rewriting Human verification',()=>{
 const t=ruleTransitions(pts,2.4,'co');
 const cor=t.find(x=>x.rule.id==='c_n_or_o');
 assert.equal(cor.gained,1);
 assert.equal(pts[1].human_result,'Non-residue');
});
