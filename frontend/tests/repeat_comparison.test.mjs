import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRepeatComparison,buildSetOverview,repeatCell,setNumber} from '../app/repeatComparisonModel.mjs';

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


test('SET overview puts time conditions in columns and SETs in rows',()=>{
 const mk=(id,set,time,result)=>({id,power:'250W',time,substrate_type:'SiCN',project_id:id,set_number:set,wafer:1,point:1,_final:result,_auto:result});
 const points=[mk('a',1,'30s','Residue'),mk('b',1,'60s','Non-residue'),mk('c',1,'120s','Ambiguous'),mk('d',2,'30s','Non-residue'),mk('e',2,'60s','Residue')];
 const o=buildSetOverview(points,'final')[0];
 assert.deepEqual(o.times,['30s','60s','120s']);
 assert.deepEqual(o.sets.map(s=>s.number),[1,2]);
 assert.equal(o.sets[0].coverage,3);
 assert.equal(o.sets[1].coverage,2);
 assert.equal(o.sets[1].cells.find(c=>c.time==='120s').n,0);
});

test('same logical SET can combine records from more than one upload batch',()=>{
 const a=p('a1',2,1,1,'Residue','batch-a');
 const b=p('a2',2,1,2,'Non-residue','batch-b');
 const g=buildRepeatComparison([a,b],'final')[0];
 assert.equal(g.sets.length,1);
 assert.equal(g.sets[0].number,2);
 assert.equal(g.sets[0].batches.length,2);
 assert.equal(g.sets[0].n,2);
});

test('location overview keeps corner edge and middle separate',()=>{
 const groups=buildSetOverview([p('a',1,1,1,'Residue'),p('b',1,4,1,'Non-residue'),p('c',1,5,1,'Ambiguous')],'final',true);
 assert.equal(groups.length,3);
 assert.deepEqual(groups.map(g=>g.wafer),[1,4,5]);
 assert.deepEqual(groups.map(g=>g.sets[0].rate),[100,0,0]);
});

test('duplicate slots are excluded from paired agreement',()=>{
 const g=buildRepeatComparison([p('a',1,1,1,'Residue'),p('b',1,1,1,'Non-residue'),p('c',2,1,1,'Residue')])[0];
 assert.equal(g.sets[0].duplicates,1);assert.equal(g.comparisons[0].paired,0);
});
