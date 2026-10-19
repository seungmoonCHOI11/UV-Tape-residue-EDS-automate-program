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
 return {version:1,name:String(raw.name||'보고서 설정').trim().slice(0,80),kind:raw.kind,filters,splitByResult:raw.splitByResult===true,includeVerification:raw.includeVerification===true,perPage:[10,12,16,20].includes(raw.perPage)?raw.perPage:12,groupScores:raw.groupScores!==false,groupConditions:raw.groupConditions!==false};
}

const pointSubstrate=p=>p?.substrate_type||p?.features?.substrate_type||'Unknown';
const pointPower=p=>String(p?.power||'-').trim();
const pointTime=p=>String(p?.time||'-').trim();
const projectId=p=>String(p?.project_id||'unknown');

/*
 * Logical SET assignment (v23.9.29)
 *
 * A SET is an experimental cycle, not an upload count. Within the same
 * substrate + Power family, different Time conditions fill the same SET as
 * long as that SET does not already contain the same Time condition.
 *
 * Example:
 *   upload A: 250W 30/60/120s -> SET1
 *   upload B: 250W 30s        -> SET2
 *   upload C: 250W 60s        -> SET2
 *   upload D: 250W 120s       -> SET2
 *
 * This also repairs historical workspaces at display time; no re-analysis is
 * required. A manually saved research_identity.repeat_index is treated as an
 * explicit override for that upload.
 */
export function assignSets(points,projects){
 const meta=new Map(projects.map(p=>[String(p.id),p])),entries=new Map();
 for(const p of points){
  const pid=projectId(p),sub=pointSubstrate(p),power=pointPower(p),time=pointTime(p),family=`${sub}|${power}`,ekey=`${family}|${pid}`;
  if(!entries.has(ekey))entries.set(ekey,{key:ekey,family,pid,sub,power,times:new Set(),createdAt:String(meta.get(pid)?.created_at||'')});
  entries.get(ekey).times.add(time);
 }
 const families=new Map();
 for(const e of entries.values()){if(!families.has(e.family))families.set(e.family,[]);families.get(e.family).push(e)}
 const assigned=new Map();
 for(const [family,items] of families){
  const occupied=new Map();
  const ordered=[...items].sort((a,b)=>a.createdAt.localeCompare(b.createdAt)||a.pid.localeCompare(b.pid));
  for(const item of ordered){
   const manual=Number(meta.get(item.pid)?.research_identity?.repeat_index);
   let n=Number.isInteger(manual)&&manual>0?manual:null;
   if(n==null){
    n=1;
    while(true){
     const used=occupied.get(n)||new Set();
     if([...item.times].every(t=>!used.has(t)))break;
     n++;
    }
   }
   if(!occupied.has(n))occupied.set(n,new Set());
   for(const t of item.times)occupied.get(n).add(t);
   assigned.set(item.key,n);
  }
 }
 return points.map(p=>{
  const pid=projectId(p),family=`${pointSubstrate(p)}|${pointPower(p)}`,n=assigned.get(`${family}|${pid}`)||1;
  return {...p,set_number:n,set_family:family,research_identity:meta.get(pid)?.research_identity||p.research_identity||{}};
 });
}
