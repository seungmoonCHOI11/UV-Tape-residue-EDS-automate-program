"""Read-only, traceable research cohorts. No image analysis or label writes."""
from collections import defaultdict
import math, re
from typing import Literal
from pydantic import BaseModel, Field
from .classification import record_result, auto_result, record_score, feature_ratios, CLASSIFICATION_RULE_TEXT

POSITIONS={1:'Corner',4:'Edge',5:'Middle',9:'Reference'}
RESULTS=('Residue','Non-residue','Ambiguous')
BANDS=('0-60','60-70','70-80','80-90','90-100','missing')
class Cohort(BaseModel):
    scope: Literal['main','reference','other']='main'
    conditions: list[str]|None=None
    substrates: list[str]|None=None
    batches: list[str]|None=None
    wafers: list[int]|None=None
    review: Literal['all','verified','unverified']='all'
    basis: Literal['final','auto']='final'
    results: list[Literal['Residue','Non-residue','Ambiguous']]|None=None
    score_bands: list[Literal['0-60','60-70','70-80','80-90','90-100','missing']]|None=None
    point_ids: list[str]|None=Field(default=None,max_length=10000)
class ExportOptions(BaseModel):
    cohort:Cohort=Field(default_factory=Cohort)
    kind:Literal['summary','points','gallery']='summary'
    format:Literal['pptx','pdf']='pptx'
    include_verification:bool=False
    per_page:Literal[10,12,16,20]=12
    group_scores:bool=True
    group_conditions:bool=True
    title:str=Field(default='UV Tape Residue Research',max_length=100)


def label(value):
    if not isinstance(value,str):return None
    k=re.sub(r'[\s_]+','-',value.strip().lower())
    return {'residue':'Residue','r':'Residue','non-residue':'Non-residue','nonresidue':'Non-residue','n':'Non-residue','ambiguous':'Ambiguous','review':'Ambiguous','unknown':'Ambiguous','a':'Ambiguous'}.get(k)
def integer(v):
    try:return int(v)
    except (ValueError,TypeError):return 0
def natural(v):return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)',str(v))]
def condition(p):return f"{p.get('power','-')} / {p.get('time','-')}"
def substrate(p):return str(p.get('substrate_type') or (p.get('features') or {}).get('substrate_type') or 'Unknown')
def decision(p,basis='final'):
    h=label(p.get('human_result'))
    return (h if basis=='final' and h else label(auto_result(p))) or 'Ambiguous'
def score(p):
    v=record_score(p)
    return max(0.,min(100.,v)) if v is not None and math.isfinite(v) else None
def band(p):
    v=score(p)
    if v is None:return 'missing'
    return '0-60' if v<60 else '60-70' if v<70 else '70-80' if v<80 else '80-90' if v<90 else '90-100'
def select(records, opts:Cohort):
    seen=set();out=[];ids=set(opts.point_ids) if opts.point_ids is not None else None
    for p in records:
        rid=str(p.get('id',''))
        if rid in seen:continue
        seen.add(rid)
        w=integer(p.get('wafer'))
        if ids is not None and rid not in ids:continue
        if opts.scope=='main' and w not in (1,4,5):continue
        if opts.scope=='reference' and w!=9:continue
        if opts.scope=='other' and w in (1,4,5,9):continue
        if opts.conditions is not None and condition(p) not in opts.conditions:continue
        if opts.substrates is not None and substrate(p) not in opts.substrates:continue
        if opts.batches is not None and str(p.get('project_id') or 'unknown') not in opts.batches:continue
        if opts.wafers is not None and w not in opts.wafers:continue
        human=bool(label(p.get('human_result')))
        if opts.review=='verified' and not human:continue
        if opts.review=='unverified' and human:continue
        if opts.results is not None and decision(p,opts.basis) not in opts.results:continue
        if opts.score_bands is not None and band(p) not in opts.score_bands:continue
        out.append(p)
    return sorted(out,key=lambda p:(natural(condition(p)),substrate(p),integer(p.get('wafer')),integer(p.get('point')),str(p.get('project_id','')),str(p.get('id',''))))
def stats(points,basis='final'):
    n=len(points);r=sum(decision(p,basis)=='Residue' for p in points);a=sum(decision(p,basis)=='Ambiguous' for p in points)
    return {'n':n,'residue':r,'non':n-r-a,'ambiguous':a,'rate':100*r/n if n else None,'upper':100*(r+a)/n if n else None,'verified':sum(bool(label(p.get('human_result'))) for p in points)}
def summary(records,opts:Cohort):
    rows=[]
    # Substrates never share a comparison stratum, even if ALL is selected.
    for sub in sorted({substrate(p) for p in records}):
        pts=[p for p in records if substrate(p)==sub]
        ws=sorted({integer(p.get('wafer')) for p in pts})
        if opts.scope=='main':ws=[w for w in (1,4,5) if opts.wafers is None or w in opts.wafers]
        keys=sorted({condition(p) for p in pts},key=natural)
        conds=[]
        for key in keys:
            a=[p for p in pts if condition(p)==key]
            cells=[{'wafer':w,'label':POSITIONS.get(w,'Other'),**stats([p for p in a if integer(p.get('wafer'))==w],opts.basis)} for w in ws]
            batches={str(p.get('project_id') or 'unknown') for p in a}
            slots={(str(p.get('project_id') or 'unknown'),integer(p.get('wafer')),integer(p.get('point'))) for p in a if 1<=integer(p.get('point'))<=9}
            expected=9*len(ws)*len(batches)
            rates=[c['rate'] for c in cells if c['n']]
            conds.append({'condition':key,**stats(a,opts.basis),'cells':cells,'slots':len(slots),'expected':expected,'coverage':100*len(slots)/expected if expected else None,'duplicates':len(a)-len({(str(p.get('project_id') or 'unknown'),integer(p.get('wafer')),integer(p.get('point'))) for p in a}),'batches':len(batches),'spread':max(rates)-min(rates) if len(rates)==len(ws) and len(ws)>1 else None})
        common=[r for r in conds if all(c['n'] for c in r['cells'])]
        pooled=[]
        for w in ws:
            cstats=[next(c for c in r['cells'] if c['wafer']==w) for r in common]
            pooled.append({'wafer':w,'label':POSITIONS.get(w,'Other'),**stats([p for p in pts if integer(p.get('wafer'))==w],opts.basis),'balanced':sum(c['rate'] for c in cstats)/len(cstats) if cstats else None,'balanced_upper':sum(c['upper'] for c in cstats)/len(cstats) if cstats else None,'common_conditions':len(common)})
        rows.append({'substrate':sub,'conditions':conds,'positions':pooled,'common_keys':[r['condition'] for r in common]})
    return {'n':len(records),'strata':rows,'rule':CLASSIFICATION_RULE_TEXT,'basis':opts.basis}
def gallery_groups(records,opts:ExportOptions):
    groups=defaultdict(list)
    for p in records:
        # Always separate final classes and substrate. Optional condition/score grouping.
        k=(substrate(p),decision(p,opts.cohort.basis),condition(p) if opts.group_conditions else 'All selected conditions',band(p) if opts.group_scores else 'All scores')
        groups[k].append(p)
    return sorted(groups.items(),key=lambda kv:(kv[0][0],kv[0][1],natural(kv[0][2]),BANDS.index(kv[0][3]) if kv[0][3] in BANDS else 0))
def page_count(records,opts):
    if opts.kind=='points':return len(records)
    if opts.kind=='gallery':return sum(math.ceil(len(a)/opts.per_page) for _,a in gallery_groups(records,opts))
    s=summary(records,opts.cohort)
    return 1+sum(math.ceil(len(x['conditions'])/5)+1 for x in s['strata'])
