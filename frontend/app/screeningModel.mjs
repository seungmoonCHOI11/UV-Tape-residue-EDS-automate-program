export const C_THRESHOLD=2.40;
export const O_THRESHOLD=3.00;
export const DEFAULT_N_THRESHOLD=2.40;
export const MODERATE_THRESHOLD=2.00;
export const SCREENING_CRITERIA=[
 {id:'co',label:'C AND O',short:'C·O',description:'기존 기준 · C와 O가 모두 강하게 증가'},
 {id:'cno',label:'C AND N AND O',short:'C·N·O',description:'C, N, O가 모두 강하게 증가'},
 {id:'cn',label:'C AND N',short:'C·N',description:'C와 N이 모두 강하게 증가'},
];
export const normalizeCriterion=v=>v==='c_n_or_o'?'cn':(SCREENING_CRITERIA.some(x=>x.id===v)?v:'co');
export const finiteNumber=v=>{if(v===null||v===undefined||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null};
export const hasHumanROI=f=>!!((Array.isArray(f?.human_roi_polygons)&&f.human_roi_polygons.some(r=>Array.isArray(r)&&r.length>=3))||(Array.isArray(f?.human_roi_polygon)&&f.human_roi_polygon.length>=3));
const ratioFrom=(f,prefix)=>{
 const direct=finiteNumber(f?.[`${prefix}_roi_global_ratio`]);if(direct!=null)return direct;
 const a=finiteNumber(f?.[`${prefix}_roi_mean`]),b=finiteNumber(f?.[`${prefix}_global_mean`]);if(a!=null&&b!=null&&b!==0)return a/b;
 const lg=finiteNumber(f?.[`${prefix}_log2_ratio`]);if(lg!=null){const v=2**lg;return Number.isFinite(v)?v:null}
 return null;
};
export function pointRatios(p){
 const f=p?.features||{};
 if(hasHumanROI(f)){
   const c=finiteNumber(f.human_c_ratio),n=finiteNumber(f.human_n_ratio),o=finiteNumber(f.human_o_ratio);
   if(c!=null&&(n!=null||o!=null))return {c,n,o,source:'Human ROI'};
 }
 return {c:ratioFrom(f,'c'),n:ratioFrom(f,'n'),o:ratioFrom(f,'o'),source:'Auto ROI'};
}
export function elementScore(ratio,strong){
 const x=finiteNumber(ratio);if(x==null)return null;strong=Math.max(MODERATE_THRESHOLD+1e-6,Number(strong)||3);
 if(x<MODERATE_THRESHOLD)return Math.max(0,Math.min(59,(x/MODERATE_THRESHOLD)*59));
 if(x<strong)return 60+((x-MODERATE_THRESHOLD)/(strong-MODERATE_THRESHOLD))*10;
 const equivalent=x*(3/strong),k=.23,denom=1-Math.exp(-k*17),normalized=(1-Math.exp(-k*(equivalent-3)))/denom;
 return Math.min(100,70+30*Math.max(0,normalized));
}
export function criterionScore(ratios,criterion='co',nThreshold=DEFAULT_N_THRESHOLD,cThreshold=C_THRESHOLD,oThreshold=O_THRESHOLD){
 criterion=normalizeCriterion(criterion);const c=elementScore(ratios?.c,cThreshold);if(c==null)return null;
 if(criterion==='co'){const o=elementScore(ratios?.o,oThreshold);return o==null?null:Math.round(Math.min(c,o)*10)/10;}
 const n=elementScore(ratios?.n,nThreshold);if(n==null)return null;
 if(criterion==='cn')return Math.round(Math.min(c,n)*10)/10;
 const o=elementScore(ratios?.o,oThreshold);return o==null?null:Math.round(Math.min(c,n,o)*10)/10;
}
export function criterionResult(ratios,criterion='co',nThreshold=DEFAULT_N_THRESHOLD,cThreshold=C_THRESHOLD,oThreshold=O_THRESHOLD){
 criterion=normalizeCriterion(criterion);const c=finiteNumber(ratios?.c),n=finiteNumber(ratios?.n),o=finiteNumber(ratios?.o);if(c==null)return null;
 let strong=false,moderate=false;
 if(criterion==='co'){if(o==null)return null;strong=c>=cThreshold&&o>=oThreshold;moderate=c>=MODERATE_THRESHOLD&&o>=MODERATE_THRESHOLD;}
 else {if(n==null)return null;if(criterion==='cn'){strong=c>=cThreshold&&n>=nThreshold;moderate=c>=MODERATE_THRESHOLD&&n>=MODERATE_THRESHOLD;}else{if(o==null)return null;strong=c>=cThreshold&&n>=nThreshold&&o>=oThreshold;moderate=c>=MODERATE_THRESHOLD&&n>=MODERATE_THRESHOLD&&o>=MODERATE_THRESHOLD;}}
 return strong?'Residue':moderate?'Ambiguous':'Non-residue';
}
export function normalizeResultLabel(v){
 if(typeof v!=='string')return null;const k=v.trim().toLowerCase().replace(/[\s_]+/g,'-');
 if(['residue','r'].includes(k))return 'Residue';if(['non-residue','nonresidue','non-res','n'].includes(k))return 'Non-residue';if(['ambiguous','review','unknown','a'].includes(k))return 'Ambiguous';return null;
}
export function autoClass(p,criterion='co',nThreshold=DEFAULT_N_THRESHOLD,cThreshold=C_THRESHOLD,oThreshold=O_THRESHOLD){
 const live=criterionResult(pointRatios(p),criterion,nThreshold,cThreshold,oThreshold);if(live)return live;
 if(normalizeCriterion(criterion)!=='co')return 'Ambiguous';
 const f=p?.features||{};for(const v of [p?.cv_result,p?.result,f.result,f.human_roi_rule_result]){const r=normalizeResultLabel(v);if(r)return r}return 'Ambiguous';
}
export function finalClass(p,criterion='co',nThreshold=DEFAULT_N_THRESHOLD,cThreshold=C_THRESHOLD,oThreshold=O_THRESHOLD){return normalizeResultLabel(p?.human_result)||autoClass(p,criterion,nThreshold,cThreshold,oThreshold)}
export function screeningScore(p,criterion='co',nThreshold=DEFAULT_N_THRESHOLD,cThreshold=C_THRESHOLD,oThreshold=O_THRESHOLD){
 const live=criterionScore(pointRatios(p),criterion,nThreshold,cThreshold,oThreshold);if(live!=null)return live;
 if(Object.prototype.hasOwnProperty.call(p||{},'evaluation_score')&&criterion==='co'&&cThreshold===C_THRESHOLD&&oThreshold===O_THRESHOLD)return p.evaluation_score;
 if(normalizeCriterion(criterion)!=='co')return null;
 const f=p?.features||{},raw=typeof f.residue_score==='number'?f.residue_score:p?.residue_score;return typeof raw==='number'&&Number.isFinite(raw)?Math.round((raw<=1.000001?raw*100:raw)*10)/10:null;
}
export function criterionMeta(criterion='co',nThreshold=DEFAULT_N_THRESHOLD,cThreshold=C_THRESHOLD,oThreshold=O_THRESHOLD){
 criterion=normalizeCriterion(criterion);const item=SCREENING_CRITERIA.find(x=>x.id===criterion)||SCREENING_CRITERIA[0];
 const rule=criterion==='cno'?`C ≥ ${Number(cThreshold).toFixed(2)}× AND N ≥ ${Number(nThreshold).toFixed(2)}× AND O ≥ ${Number(oThreshold).toFixed(2)}×`:criterion==='cn'?`C ≥ ${Number(cThreshold).toFixed(2)}× AND N ≥ ${Number(nThreshold).toFixed(2)}×`:`C ≥ ${Number(cThreshold).toFixed(2)}× AND O ≥ ${Number(oThreshold).toFixed(2)}×`;
 return {...item,rule,needsN:criterion!=='co'};
}
