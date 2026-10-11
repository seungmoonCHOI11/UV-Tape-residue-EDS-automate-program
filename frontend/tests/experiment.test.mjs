import test from 'node:test';
import assert from 'node:assert/strict';
import {cleanPreset,ratioPoints,identityText} from '../app/experimentModel.mjs';
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
