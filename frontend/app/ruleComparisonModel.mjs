import {SCREENING_CRITERIA,autoClass,criterionScore,normalizeResultLabel,pointRatios} from './screeningModel.mjs';
import {condition,substrate,natural,LOCATIONS} from './researchModel.mjs';

export function autoRuleStats(points,criterion,nThreshold=2.40,cThreshold=2.40,oThreshold=3.00){
 const labels=points.map(p=>autoClass(p,criterion,nThreshold,cThreshold,oThreshold));
 const n=points.length,residue=labels.filter(x=>x==='Residue').length,ambiguous=labels.filter(x=>x==='Ambiguous').length,non=n-residue-ambiguous;
 const verified=points.filter(p=>normalizeResultLabel(p?.human_result));
 const agreement=verified.length?100*verified.filter(p=>autoClass(p,criterion,nThreshold,cThreshold,oThreshold)===normalizeResultLabel(p.human_result)).length/verified.length:null;
 const nAvailable=points.filter(p=>pointRatios(p).n!=null).length;
 return {n,residue,non,ambiguous,rate:n?100*residue/n:null,upper:n?100*(residue+ambiguous)/n:null,verified:verified.length,agreement,nAvailable};
}

export function ruleDistributions(points,nThreshold=2.40,cThreshold=2.40,oThreshold=3.00){
 return SCREENING_CRITERIA.map(rule=>({rule,...autoRuleStats(points,rule.id,nThreshold,cThreshold,oThreshold)}));
}

export function ruleTransitions(points,nThreshold=2.40,baseline='co',cThreshold=2.40,oThreshold=3.00){
 const base=new Map(points.map(p=>[p.id,autoClass(p,baseline,nThreshold,cThreshold,oThreshold)]));
 return SCREENING_CRITERIA.filter(r=>r.id!==baseline).map(rule=>{
  let same=0,gained=0,lost=0,changed=0;
  for(const p of points){const a=base.get(p.id),b=autoClass(p,rule.id,nThreshold,cThreshold,oThreshold);if(a===b)same++;else{changed++;if(a!=='Residue'&&b==='Residue')gained++;if(a==='Residue'&&b!=='Residue')lost++;}}
  return {rule,same,changed,gained,lost,n:points.length};
 });
}

export function buildRuleStrata(points,filters,nThreshold=2.40,cThreshold=2.40,oThreshold=3.00){
 return [...new Set(points.map(substrate))].sort(natural).map(sub=>{
  const pts=points.filter(p=>substrate(p)===sub);
  const ws=filters.scope==='main'?[1,4,5].filter(w=>filters.wafers===null||filters.wafers.includes(w)):[...new Set(pts.map(p=>Number(p.wafer)))].sort((a,b)=>a-b);
  const conditionRows=[...new Set(pts.map(condition))].sort(natural).map(key=>{
   const a=pts.filter(p=>condition(p)===key);
   const cells=ws.map(w=>{const cellPoints=a.filter(p=>Number(p.wafer)===w);return {wafer:w,label:LOCATIONS[w]||'Other',points:cellPoints,rules:Object.fromEntries(SCREENING_CRITERIA.map(rule=>[rule.id,autoRuleStats(cellPoints,rule.id,nThreshold,cThreshold,oThreshold)]))};});
   const rules={};
   for(const rule of SCREENING_CRITERIA){const s=autoRuleStats(a,rule.id,nThreshold,cThreshold,oThreshold),rates=cells.map(c=>c.rules[rule.id]).filter(x=>x.n).map(x=>x.rate);rules[rule.id]={...s,spread:rates.length===ws.length&&rates.length>1?Math.max(...rates)-Math.min(...rates):null};}
   return {key,points:a,cells,rules};
  });
  const positions=ws.map(w=>{const a=pts.filter(p=>Number(p.wafer)===w);return {wafer:w,label:LOCATIONS[w]||'Other',points:a,rules:Object.fromEntries(SCREENING_CRITERIA.map(rule=>[rule.id,autoRuleStats(a,rule.id,nThreshold,cThreshold,oThreshold)]))};});
  return {substrate:sub,points:pts,conditions:conditionRows,positions,rules:Object.fromEntries(SCREENING_CRITERIA.map(rule=>[rule.id,autoRuleStats(pts,rule.id,nThreshold,cThreshold,oThreshold)]))};
 });
}

const csvCell=v=>'"'+(/^[=+@\-\t\r]/.test(String(v??''))?"'":'')+String(v??'').replaceAll('"','""')+'"';
export function ruleComparisonCsv(points,nThreshold=2.40,cThreshold=2.40,oThreshold=3.00){
 const rows=[['Point ID','Batch','Condition','Wafer','Location','Point','Substrate','Human result','C ratio','N ratio','O ratio',...SCREENING_CRITERIA.flatMap(r=>[`Auto ${r.short}`,`Score ${r.short}`])],...points.map(p=>{const r=pointRatios(p);return [p.id,p.project_id,condition(p),p.wafer,LOCATIONS[p.wafer]||'Other',p.point,substrate(p),normalizeResultLabel(p.human_result)||'',r.c,r.n,r.o,...SCREENING_CRITERIA.flatMap(rule=>[autoClass(p,rule.id,nThreshold,cThreshold,oThreshold),criterionScore(r,rule.id,nThreshold,cThreshold,oThreshold)])]})];
 return '\uFEFF'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n');
}
