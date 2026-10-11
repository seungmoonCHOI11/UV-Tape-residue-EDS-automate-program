import test from 'node:test';import assert from 'node:assert/strict';
import {fetchWorkspacePages} from '../app/workspaceLoader.mjs';
test('81 points are fetched in bounded pages and committed together',async()=>{
 const requested=[];const points=Array.from({length:81},(_,id)=>({id:String(id)}));
 const result=await fetchWorkspacePages('http://mock',async url=>{const offset=Number(new URL(url).searchParams.get('offset'));requested.push(offset);return {ok:true,json:async()=>({project_id:'p',points:points.slice(offset,offset+60),has_more:offset+60<81,next_offset:offset+60})}});
 assert.equal(result.points.length,81);assert.deepEqual(requested,[0,60]);
});
test('nonadvancing cursor is rejected instead of partial success',async()=>{
 await assert.rejects(fetchWorkspacePages('http://mock',async()=>({ok:true,json:async()=>({points:[],has_more:true,next_offset:0})})),/cursor/);
});
test('old backend response without pagination remains compatible',async()=>{
 const result=await fetchWorkspacePages('http://mock',async()=>({ok:true,json:async()=>({points:[{id:'a'}]})}));assert.equal(result.points.length,1);
});
