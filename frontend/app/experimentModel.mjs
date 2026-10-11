import {DEFAULT_FILTERS} from './researchModel.mjs';
export const identityText=p=>{const x=p.research_identity||{};return [x.batch_label||String(p.project_id||'배치 미지정').slice(0,12),x.sample_id?`시료 ${x.sample_id}`:'시료 미지정',x.repeat_index?`반복 ${x.repeat_index}`:null,x.measured_on].filter(Boolean).join(' · ')};
export const imageSource=(p,key,api)=>{const src=p.assets?.[key];if(typeof src!=='string'||!src)return null;return /^(https?:|data:|blob:)/.test(src)?src:src.startsWith('/')?api+src:`${api}/api/assets/${encodeURIComponent(src)}`};
export const ratioPoints=(points,ratios)=>points.map(p=>({...p,...ratios(p)})).filter(p=>typeof p.c==='number'&&Number.isFinite(p.c)&&p.c>=0&&typeof p.o==='number'&&Number.isFinite(p.o)&&p.o>=0);
export function cleanPreset(raw){
 if(!raw||typeof raw!=='object'||!['summary','points','gallery'].includes(raw.kind))throw Error('지원하지 않는 보고서 설정입니다.');
 const f=raw.filters||{},filters={...DEFAULT_FILTERS};
 for(const k of ['conditions','substrates','batches'])if(Array.isArray(f[k])&&f[k].length<=1000&&f[k].every(x=>typeof x==='string'&&x.length<300))filters[k]=f[k];
 if(['main','reference','other'].includes(f.scope))filters.scope=f.scope;
 if(['all','verified','unverified'].includes(f.review))filters.review=f.review;
 if(['final','auto'].includes(f.basis))filters.basis=f.basis;
 if(Array.isArray(f.wafers)&&f.wafers.every(x=>Number.isInteger(x)&&x>=1&&x<=9))filters.wafers=f.wafers;
 for(const [key,allowed] of [['results',['Residue','Non-residue','Ambiguous']],['score_bands',['0-60','60-70','70-80','80-90','90-100','missing']]])if(Array.isArray(f[key])&&f[key].every(x=>allowed.includes(x)))filters[key]=f[key];
 return {version:1,name:String(raw.name||'보고서 설정').trim().slice(0,80),kind:raw.kind,filters,includeVerification:raw.includeVerification===true,perPage:[10,12,16,20].includes(raw.perPage)?raw.perPage:12,groupScores:raw.groupScores!==false,groupConditions:raw.groupConditions!==false};
}

export function assignSets(points,projects){
 const meta=new Map(projects.map(p=>[String(p.id),p])),groups=new Map();
 for(const p of points){const key=`${p.power} / ${p.time}`;if(!groups.has(key))groups.set(key,new Set());groups.get(key).add(String(p.project_id||'unknown'));}
 const indices=new Map();
 for(const [key,ids] of groups){const used=new Set();const ordered=[...ids].sort((a,b)=>String(meta.get(a)?.created_at||'').localeCompare(String(meta.get(b)?.created_at||''))||a.localeCompare(b));for(const id of ordered){const saved=Number(meta.get(id)?.repeat_no);let n=Number.isInteger(saved)&&saved>0&&!used.has(saved)?saved:Math.max(0,...used)+1;used.add(n);indices.set(`${key}|${id}`,n)}}
 return points.map(p=>({...p,set_number:indices.get(`${p.power} / ${p.time}|${p.project_id||'unknown'}`),research_identity:meta.get(String(p.project_id))?.research_identity||{}}));
}
