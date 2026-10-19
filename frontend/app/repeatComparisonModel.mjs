import {condition,substrate,decision,natural,LOCATIONS} from './researchModel.mjs';

export const setNumber=p=>{
 const a=Number(p?.set_number),b=Number(p?.research_identity?.repeat_index);
 if(Number.isInteger(a)&&a>0)return a;
 if(Number.isInteger(b)&&b>0)return b;
 return 1;
};

const resultCounts=(points,basis)=>{
 const n=points.length,residue=points.filter(p=>decision(p,basis)==='Residue').length,ambiguous=points.filter(p=>decision(p,basis)==='Ambiguous').length;
 return {n,residue,non:n-residue-ambiguous,ambiguous,rate:n?100*residue/n:null};
};

const waferOrder=w=>({1:1,4:2,5:3,9:4}[Number(w)]||100+Number(w));
const slotKey=p=>`${Number(p.wafer)}|${Number(p.point)}`;

export function buildRepeatComparison(points,basis='final'){
 const grouped=new Map();
 for(const p of points){
  const key=`${substrate(p)}|${condition(p)}`;
  if(!grouped.has(key))grouped.set(key,[]);
  grouped.get(key).push(p);
 }
 return [...grouped.entries()].map(([key,pts])=>{
  const bySet=new Map();
  for(const p of pts){
   const n=setNumber(p),batch=String(p.project_id||'unknown'),skey=`${n}|${batch}`;
   if(!bySet.has(skey))bySet.set(skey,{number:n,batch,points:[]});
   bySet.get(skey).points.push(p);
  }
  const sets=[...bySet.values()].sort((a,b)=>a.number-b.number||natural(a.batch,b.batch)).map(s=>{
   const slots=new Map();
   for(const p of s.points){const k=slotKey(p);if(!slots.has(k))slots.set(k,[]);slots.get(k).push(p)}
   return {...s,...resultCounts(s.points,basis),slots,duplicates:[...slots.values()].reduce((n,a)=>n+Math.max(0,a.length-1),0)};
  });
  const wafers=[...new Set(pts.map(p=>Number(p.wafer)))].sort((a,b)=>waferOrder(a)-waferOrder(b));
  const baseline=sets[0];
  const comparisons=baseline?sets.slice(1).map(s=>{
   const common=[...baseline.slots.keys()].filter(k=>s.slots.has(k));
   let same=0,gained=0,lost=0;
   for(const k of common){
    const a=baseline.slots.get(k)[0],b=s.slots.get(k)[0],da=decision(a,basis),db=decision(b,basis);
    if(da===db)same++;
    if(da!=='Residue'&&db==='Residue')gained++;
    if(da==='Residue'&&db!=='Residue')lost++;
   }
   return {from:baseline.number,to:s.number,fromBatch:baseline.batch,toBatch:s.batch,paired:common.length,same,agreement:common.length?100*same/common.length:null,gained,lost,deltaRate:baseline.rate==null||s.rate==null?null:s.rate-baseline.rate};
  }):[];
  return {key,substrate:substrate(pts[0]),condition:condition(pts[0]),points:pts,sets,wafers,comparisons};
 }).sort((a,b)=>natural(a.substrate,b.substrate)||natural(a.condition,b.condition));
}

export function repeatCell(set,wafer,point,basis='final'){
 const a=set?.slots?.get(`${Number(wafer)}|${Number(point)}`)||[];
 if(!a.length)return {points:[],result:null,label:'—',className:'missing'};
 if(a.length>1)return {points:a,result:null,label:`${a.length}건`,className:'duplicate'};
 const result=decision(a[0],basis),label=result==='Residue'?'R':result==='Non-residue'?'N':'A',className=result==='Residue'?'res':result==='Non-residue'?'non':'amb';
 return {points:a,result,label,className};
}

export function repeatLocationLabel(w){return `W${w} · ${LOCATIONS[w]||'Other'}`}
