import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRepeatComparison,repeatCell,setNumber} from '../app/repeatComparisonModel.mjs';

const p=(id,set,wafer,point,result,batch=`b${set}`)=>({id,power:'150W',time:'30s',substrate_type:'SiCN',project_id:batch,set_number:set,wafer,point,_final:result,_auto:result,research_identity:{batch_label:`SET${set}`}});

test('same condition is grouped into ordered SET rows',()=>{
 const points=[p('a1',1,1,1,'Residue'),p('a2',1,1,2,'Non-residue'),p('b1',2,1,1,'Residue'),p('b2',2,1,2,'Residue')];
 const groups=buildRepeatComparison(points,'final');
 assert.equal(groups.length,1);
 const g=groups[0];
 assert.deepEqual(g.sets.map(s=>s.number),[1,2]);
 assert.equal(g.sets[0].residue,1);
 assert.equal(g.sets[1].residue,2);
 assert.equal(g.comparisons[0].paired,2);
 assert.equal(g.comparisons[0].same,1);
 assert.equal(g.comparisons[0].gained,1);
 assert.equal(g.comparisons[0].lost,0);
 assert.equal(g.comparisons[0].agreement,50);
 assert.equal(g.comparisons[0].deltaRate,50);
});

test('matrix cells align SET1 and SET2 by the same W/P slot',()=>{
 const points=[p('a1',1,4,5,'Ambiguous'),p('b1',2,4,5,'Non-residue')];
 const g=buildRepeatComparison(points,'final')[0];
 assert.equal(repeatCell(g.sets[0],4,5,'final').label,'A');
 assert.equal(repeatCell(g.sets[1],4,5,'final').label,'N');
 assert.equal(repeatCell(g.sets[1],4,6,'final').label,'—');
});

test('saved set_number has priority and repeat_index is fallback',()=>{
 assert.equal(setNumber({set_number:2,research_identity:{repeat_index:9}}),2);
 assert.equal(setNumber({research_identity:{repeat_index:3}}),3);
 assert.equal(setNumber({}),1);
});
