import test from 'node:test';
import assert from 'node:assert/strict';
import {cleanPreset,ratioPoints,identityText,assignSets} from '../app/experimentModel.mjs';
test('scatter excludes missing and invalid values without losing real zeros',()=>{
 const a=[{id:'0',c:0,o:0},{id:'1',c:null,o:3},{id:'2',c:Infinity,o:3},{id:'3',c:-1,o:3}];
 assert.deepEqual(ratioPoints(a,p=>({c:p.c,o:p.o})).map(p=>p.id),['0']);
});
test('portable presets do not pin historical Point IDs',()=>{
 const p=cleanPreset({kind:'gallery',filters:{point_ids:['old'],results:['Residue'],score_bands:['80-90','90-100']},perPage:20});
 assert.equal(p.filters.point_ids,undefined);assert.deepEqual(p.filters.results,['Residue']);assert.equal(p.perPage,20);
 assert.throws(()=>cleanPreset({kind:'unknown'}));
 assert.equal(cleanPreset({kind:'points',filters:{scope:'all'},perPage:999}).filters.scope,'main');
});
test('upload repeat counter never becomes experimental repeat identity',()=>{
 assert(!identityText({project_id:'batch',repeat_no:7}).includes('반복'));
 assert(identityText({project_id:'batch',research_identity:{sample_id:'S1',repeat_index:2}}).includes('반복 2'));
});


test('separate time uploads fill the same logical SET instead of incrementing per upload',()=>{
 const projects=[
  {id:'full',created_at:'2026-01-01T00:00:00Z',research_identity:{}},
  {id:'t30',created_at:'2026-01-02T00:00:00Z',research_identity:{}},
  {id:'t60',created_at:'2026-01-03T00:00:00Z',research_identity:{}},
  {id:'t120',created_at:'2026-01-04T00:00:00Z',research_identity:{}}
 ];
 const mk=(project_id,time,id)=>({id,project_id,power:'250W',time,substrate_type:'SiCN'});
 const points=[mk('full','30s','a'),mk('full','60s','b'),mk('full','120s','c'),mk('t30','30s','d'),mk('t60','60s','e'),mk('t120','120s','f')];
 const out=assignSets(points,projects);
 const byId=Object.fromEntries(out.map(p=>[p.id,p.set_number]));
 assert.deepEqual(byId,{a:1,b:1,c:1,d:2,e:2,f:2});
});

test('logical SET filling is independent for substrate and power families',()=>{
 const projects=[{id:'a',created_at:'2026-01-01',research_identity:{}},{id:'b',created_at:'2026-01-02',research_identity:{}}];
 const points=[
  {id:'1',project_id:'a',power:'250W',time:'30s',substrate_type:'SiCN'},
  {id:'2',project_id:'b',power:'250W',time:'30s',substrate_type:'Si'},
  {id:'3',project_id:'b',power:'350W',time:'30s',substrate_type:'SiCN'}
 ];
 const out=assignSets(points,projects);
 assert(out.every(p=>p.set_number===1));
});

test('manual experimental repeat index overrides automatic SET filling',()=>{
 const projects=[
  {id:'a',created_at:'2026-01-01',research_identity:{}},
  {id:'b',created_at:'2026-01-02',research_identity:{repeat_index:4}}
 ];
 const points=[
  {id:'1',project_id:'a',power:'250W',time:'30s',substrate_type:'SiCN'},
  {id:'2',project_id:'b',power:'250W',time:'60s',substrate_type:'SiCN'}
 ];
 const out=assignSets(points,projects);
 assert.equal(out.find(p=>p.id==='1').set_number,1);
 assert.equal(out.find(p=>p.id==='2').set_number,4);
});
