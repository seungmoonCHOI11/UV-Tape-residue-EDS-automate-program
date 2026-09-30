"""Memory-minimal one-Point PDF/SEM/EDS worker for v16.

v16 changes:
- keeps the verified v13 Point-1 crop boundaries;
- detects the ROI from the higher-resolution Page-1 SEM crop, not the Page-2 SE crop;
- excludes the SEM footer/scale-bar region from detection and global statistics;
- compares ROI signal with the whole analytical image (global robust baseline), not a local ring;
- keeps the yellow ring as a visual reference only, never as a classification baseline;
- produces an always-available 0..1 residue score and detailed global-vs-ROI diagnostics.
"""
import gc, json, os, sys
from pathlib import Path
os.environ.setdefault("OMP_NUM_THREADS","1"); os.environ.setdefault("OPENBLAS_NUM_THREADS","1"); os.environ.setdefault("MKL_NUM_THREADS","1"); os.environ.setdefault("NUMEXPR_NUM_THREADS","1"); os.environ.setdefault("OPENCV_OPENCL_RUNTIME","disabled")
import cv2, numpy as np, pymupdf as fitz
cv2.setNumThreads(1)
try: cv2.ocl.setUseOpenCL(False)
except Exception: pass


def render_page_to_jpeg(page, quality=92):
    pix=page.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
    try: data=pix.tobytes("jpeg",jpg_quality=quality)
    finally: del pix
    return data


def crop_page1_layout(img):
    """Verified v13 Point-1 crop boundaries, fixed to the source page template."""
    h,w=img.shape[:2]; sx,sy=w/1240.0,h/1754.0
    def box(x0,y0,x1,y1):
        return img[round(y0*sy):round(y1*sy),round(x0*sx):round(x1*sx)]
    return {"sem":box(98,207,98+628,207+418), "eds_map":box(101,818,101+1102,818+734)}


def crop_page2_element_maps(img):
    """Verified v13 Point-1 element-panel boundaries."""
    h,w=img.shape[:2]; sx,sy=w/1240.0,h/1754.0
    def box(x0,y0,x1,y1):
        return img[round(y0*sy):round(y1*sy),round(x0*sx):round(x1*sx)]
    return {
        "full_element_maps_original":box(101,190,1200,1305),
        "se_map":box(101,190,643,552), "c_map":box(658,190,1200,552),
        "n_map":box(101,566,643,928), "o_map":box(658,566,1200,928),
        "si_map":box(101,943,643,1305),
    }


def save_crop(img,path,quality=90):
    path.parent.mkdir(parents=True,exist_ok=True); cv2.imwrite(str(path),img,[int(cv2.IMWRITE_JPEG_QUALITY),quality])


def resize_like(im,shape):
    h,w=shape[:2]
    return im if im.shape[:2]==(h,w) else cv2.resize(im,(w,h),interpolation=cv2.INTER_AREA)


def signal_channel(im,element):
    b,g,r=cv2.split(im)
    if element=="C": return r
    if element=="N": return g
    if element=="O": return b
    if element=="Si": return cv2.max(g,b)
    return cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)


def analytical_mask(shape, footer_fraction=0.84):
    """Mask of useful analytical image area; bottom SEM/EDS metadata is excluded."""
    h,w=shape[:2]
    m=np.zeros((h,w),np.uint8)
    m[:max(1,int(h*footer_fraction)),:]=255
    # avoid a tiny border where labels/scale bars can touch the crop edge
    m[:3,:]=0; m[-3:,:]=0; m[:,:3]=0; m[:,-3:]=0
    return m


def _candidate_score(gray, labels, stats, idx, background, analysis_mask):
    x,y,ww,hh,area=stats[idx]
    total=float(np.count_nonzero(analysis_mask))
    if area < max(35,int(total*0.00008)) or area > int(total*0.18): return -1e9
    if x<=3 or y<=3 or x+ww>=gray.shape[1]-3 or y+hh>=int(gray.shape[0]*.84)-3: return -1e9
    aspect=max(ww/max(hh,1),hh/max(ww,1))
    if aspect>16: return -1e9
    fill=area/max(ww*hh,1)
    comp=(4*np.pi*area/max(cv2.arcLength(cv2.findContours((labels==idx).astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0][0],True),1)**2,1) if False else 0
    ys,xs=np.where(labels==idx)
    vals=gray[ys,xs].astype(np.float32)
    local_bg=background[ys,xs].astype(np.float32)
    contrast=float(np.median(vals-local_bg))/max(float(np.std(background[analysis_mask>0])),8.0)
    edge=float(np.mean(cv2.Canny(gray,50,120)[ys,xs]>0))
    # Prefer a real compact/high-contrast object, but do not make area the dominant term.
    area_term=np.clip(np.log1p(area)/9.0,0,1)
    fill_term=np.clip(fill/0.55,0,1)
    contrast_term=np.clip((contrast+0.5)/4.0,0,1)
    edge_term=np.clip(edge/0.35,0,1)
    return 0.36*contrast_term+0.26*fill_term+0.18*area_term+0.12*edge_term+0.08*np.clip(np.sqrt(area/max(total,1))*12,0,1)


def detect_residue_candidates(sem, max_candidates=8):
    """Return plausible SEM physical candidates using a multi-scale top-hat cue.

    Top-hat subtraction is intentionally used instead of a broad normalized-background
    threshold: the latter can merge a large bright residue into the page background.
    The candidate list is later checked against C/O global-vs-ROI evidence.
    """
    gray=cv2.cvtColor(sem,cv2.COLOR_BGR2GRAY) if sem.ndim==3 else sem.copy()
    h,w=gray.shape; work=gray[:int(h*.84),:]
    clahe=cv2.createCLAHE(clipLimit=1.6,tileGridSize=(8,8)).apply(work)
    cues=[]
    for k in (21,31,45):
        kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k))
        top=cv2.morphologyEx(clahe,cv2.MORPH_TOPHAT,kernel)
        top=cv2.GaussianBlur(top,(3,3),0)
        _,m=cv2.threshold(top,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        m=cv2.morphologyEx(m,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
        cues.append(m)
    mask=cv2.bitwise_or(cues[0],cv2.bitwise_or(cues[1],cues[2]))
    n,labels,stats,_=cv2.connectedComponentsWithStats(mask)
    background=cv2.GaussianBlur(clahe,(0,0),17)
    edge_map=cv2.Canny(clahe,50,120)
    total=float(work.shape[0]*work.shape[1]); cand=[]
    for i in range(1,n):
        x,y,ww,hh,area=stats[i]
        if area < max(35,int(total*0.00008)) or area > int(total*0.20): continue
        if x<=3 or y<=3 or x+ww>=w-3 or y+hh>=int(h*.84)-3: continue
        aspect=max(ww/max(hh,1),hh/max(ww,1))
        if aspect>18: continue
        ys,xs=np.where(labels==i); vals=clahe[ys,xs].astype(np.float32); bgv=background[ys,xs].astype(np.float32)
        contrast=float(np.median(vals-bgv))/max(float(np.std(background)),8.0)
        fill=area/max(ww*hh,1); edge=float(np.mean(edge_map[ys,xs]>0))
        area_term=np.clip(np.log1p(area)/9.0,0,1); fill_term=np.clip(fill/.55,0,1); contrast_term=np.clip((contrast+.3)/3.5,0,1); edge_term=np.clip(edge/.35,0,1)
        morph_rank=.48*area_term+.27*contrast_term+.15*fill_term+.10*edge_term
        cm=np.zeros_like(gray,np.uint8); cm[ys,xs]=255
        cand.append({"label":i,"score":float(morph_rank),"area":int(area),"bbox":(int(x),int(y),int(ww),int(hh)),"mask":cm})
    cand.sort(key=lambda z:z["score"],reverse=True)
    # Keep spatially distinct candidates; do not collapse nearby fragments into one giant box.
    out=[]
    for c in cand:
        x,y,ww,hh=c["bbox"]; cx=x+ww/2; cy=y+hh/2
        if any(np.hypot((cx-d["cx"])/max(d["ww"],20),(cy-d["cy"])/max(d["hh"],20))<.55 for d in out): continue
        c={**c,"cx":cx,"cy":cy,"ww":ww,"hh":hh}; out.append(c)
        if len(out)>=max_candidates: break
    return out

def detect_residue_mask(sem):
    """Backward-compatible single-candidate helper; final selection occurs in residue_features."""
    c=detect_residue_candidates(sem,1)
    return c[0]["mask"] if c else np.zeros(sem.shape[:2],np.uint8)

def roi_box_from_mask(mask, shape):
    """Create the v3-style rectangular ROI around the detected physical candidate."""
    h,w=shape[:2]; ys,xs=np.where(mask>0)
    if len(xs)==0: return np.zeros((h,w),np.uint8)
    x0,x1=int(xs.min()),int(xs.max()+1); y0,y1=int(ys.min()),int(ys.max()+1)
    ww=max(x1-x0,12); hh=max(y1-y0,12)
    # More generous padding than v15 so elongated/tail residues are less likely to be clipped.
    px=max(18,int(ww*.36)); py=max(16,int(hh*.55))
    x0=max(5,x0-px); x1=min(w-5,x1+px); y0=max(5,y0-py); y1=min(int(h*.84),y1+py)
    # Avoid a tiny ROI; v3 reference is intentionally a contextual rectangle.
    if x1-x0<60:
        c=(x0+x1)//2; x0=max(5,c-30); x1=min(w-5,c+30)
    if y1-y0<45:
        c=(y0+y1)//2; y0=max(5,c-23); y1=min(int(h*.84),c+23)
    box=np.zeros((h,w),np.uint8); cv2.rectangle(box,(x0,y0),(x1-1,y1-1),255,-1); return box


def global_roi_metrics(element_img,roi,element,footer_fraction=.84):
    """Compare ROI against the whole analytical image, never against the yellow ring."""
    sig=signal_channel(element_img,element).astype(np.float32)
    am=analytical_mask(sig.shape,footer_fraction)>0
    rb=(roi>0)&am
    if not np.any(rb):
        return {"contrast_pct":0.0,"log2_ratio":0.0,"zscore":0.0,"coverage":0.0,"overlap":0.0,"roi_mean":0.0,"global_mean":0.0,"global_std":0.0,"score":0.0}
    vals=sig[am]; inside=sig[rb]
    gmed=float(np.median(vals)); gmad=float(np.median(np.abs(vals-gmed))); gstd=max(1.4826*gmad,1.0)
    rmed=float(np.median(inside));
    ratio=(rmed+1.0)/(gmed+1.0); log2=float(np.log2(max(ratio,1e-6)))
    contrast=float((rmed-gmed)/(gmed+1.0)*100)
    z=float((rmed-gmed)/gstd)
    # Robust percentile of pixels in ROI relative to the entire analytical field.
    p95=float(np.percentile(vals,95)); high=sig>=p95
    coverage=float(np.mean(high[rb])*100)
    # Main signal score is global robust z/log-ratio; percentile coverage is supporting evidence.
    score=float(np.clip(.62*(1/(1+np.exp(-np.clip((z-0.75)/1.0,-20,20)))) + .38*(1/(1+np.exp(-np.clip((log2-0.10)/0.28,-20,20)))),0,1))
    return {"contrast_pct":round(float(np.clip(contrast,-100,500)),2),"log2_ratio":round(log2,4),"zscore":round(float(np.clip(z,-20,20)),3),"coverage":round(coverage,2),"overlap":round(coverage,2),"roi_mean":round(rmed,3),"global_mean":round(gmed,3),"global_std":round(gstd,3),"score":round(score,4)}


def sigmoid(x): return 1/(1+np.exp(-np.clip(x,-20,20)))


def make_box_overlay(base,roi,roi_color=(0,0,255),ring_color=(0,220,255)):
    out=base.copy()
    if out.ndim==2: out=cv2.cvtColor(out,cv2.COLOR_GRAY2BGR)
    ys,xs=np.where(roi>0)
    if len(xs)==0:return out
    x0,x1=int(xs.min()),int(xs.max()); y0,y1=int(ys.min()),int(ys.max())
    # Visual-only ring. It is deliberately not used for classification.
    ring=cv2.dilate(roi,np.ones((21,21),np.uint8),iterations=1); ring=((ring>0)&(roi==0)).astype(np.uint8)*255
    contours,_=cv2.findContours(ring,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE); cv2.drawContours(out,contours,-1,ring_color,2)
    cv2.rectangle(out,(x0,y0),(x1,y1),roi_color,3); return out


def make_overlay(base,mask,title=None): return make_box_overlay(base,mask)
def make_roi_ring_overlay(base,roi,roi_color=(0,0,255),ring_color=(0,220,255)): return make_box_overlay(base,roi,roi_color,ring_color)


def enhance_element_map(im,element):
    ch=signal_channel(im,element); p2,p98=np.percentile(ch,[2,98]); norm=np.clip((ch.astype(np.float32)-p2)*255/max(p98-p2,1),0,255).astype(np.uint8); norm=cv2.createCLAHE(clipLimit=2.2,tileGridSize=(8,8)).apply(norm)
    if element=="C": return cv2.merge([np.zeros_like(norm),np.zeros_like(norm),norm])
    if element=="N": return cv2.merge([np.zeros_like(norm),norm,np.zeros_like(norm)])
    if element=="O": return cv2.merge([norm,np.zeros_like(norm),np.zeros_like(norm)])
    if element=="Si": return cv2.merge([norm,norm,np.zeros_like(norm)])
    return cv2.cvtColor(norm,cv2.COLOR_GRAY2BGR)


def make_element_overview(maps,roi):
    items=[("C",maps["c_map"]),("N",maps["n_map"]),("O",maps["o_map"]),("Si",maps["si_map"])]
    th=min(im.shape[0] for _,im in items); tw=min(im.shape[1] for _,im in items); cells=[]
    for label,im in items:
        enh=cv2.resize(enhance_element_map(im,label),(tw,th),interpolation=cv2.INTER_AREA); r=cv2.resize(roi,(tw,th),interpolation=cv2.INTER_NEAREST); cells.append(make_overlay(enh,r,label))
    return np.vstack([np.hstack([cells[0],cells[1]]),np.hstack([cells[2],cells[3]])])


def residue_features(sem_ref,c_map,o_map,n_map,si_map):
    """Select a SEM ROI, then compare that ROI with the whole analytical field.

    Candidate selection uses SEM morphology first and C/O global evidence only to choose
    between plausible SEM candidates. Classification itself never uses the Local Ring.
    """
    candidates=detect_residue_candidates(sem_ref,8)
    h,w=sem_ref.shape[:2]
    gray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY)
    am=analytical_mask(gray.shape)>0
    gmed=float(np.median(gray[am])); gmad=float(np.median(np.abs(gray[am]-gmed))); gstd=max(1.4826*gmad,1.0)
    scored=[]
    for c in candidates:
        roi=roi_box_from_mask(c["mask"],sem_ref.shape)
        roi_c=cv2.resize(roi,(c_map.shape[1],c_map.shape[0]),interpolation=cv2.INTER_NEAREST)
        roi_o=cv2.resize(roi,(o_map.shape[1],o_map.shape[0]),interpolation=cv2.INTER_NEAREST)
        cm=global_roi_metrics(c_map,roi_c,"C"); om=global_roi_metrics(o_map,roi_o,"O")
        candidate_area=int(cv2.countNonZero(c["mask"])); roi_area=max(int(cv2.countNonZero(roi)),1)
        vals=gray[c["mask"]>0].astype(np.float32) if candidate_area else np.array([])
        morph_z=(float(np.median(vals))-gmed)/gstd if vals.size else 0.0
        coverage=candidate_area/roi_area
        morphology_score=float(np.clip(sigmoid((morph_z-.9)/1.4)*.72+np.clip(coverage/.28,0,1)*.28,0,1)) if candidate_area else 0.0
        spatial=float(np.clip(min(cm["coverage"],om["coverage"])/55.0,0,1))
        selection=float(np.clip(.34*morphology_score+.30*cm["score"]+.28*om["score"]+.08*spatial,0,1))
        scored.append((selection,c,roi,cm,om,morphology_score,morph_z,spatial))
    if not scored:
        roi=np.zeros((h,w),np.uint8); return {
            "result":"Review","confidence":"Low","residue_score":0.0,"c_enrichment":0.0,"o_enrichment":0.0,
            "c_log2_ratio":0.0,"o_log2_ratio":0.0,"c_zscore":0.0,"o_zscore":0.0,"c_score":0.0,"o_score":0.0,
            "c_coverage":0.0,"o_coverage":0.0,"c_spatial_overlap":0.0,"o_spatial_overlap":0.0,"spatial_overlap":0.0,
            "morphology_score":0.0,"cluster_score":0.0,"roi_area_px":0,"candidate_area_px":0,"candidate_coverage":0.0,
            "review_reasons":["no_reliable_SEM_candidate"],"classification_note":"No defensible SEM candidate was detected; point requires human review."
        },roi
    scored.sort(key=lambda z:z[0],reverse=True)
    _,chosen,roi,cm,om,morphology_score,morph_z,spatial=scored[0]
    # N/Si are diagnostics only.
    rrn=cv2.resize(roi,(n_map.shape[1],n_map.shape[0]),interpolation=cv2.INTER_NEAREST)
    rrs=cv2.resize(roi,(si_map.shape[1],si_map.shape[0]),interpolation=cv2.INTER_NEAREST)
    nm=global_roi_metrics(n_map,rrn,"N"); sm=global_roi_metrics(si_map,rrs,"Si")
    ce,oe=cm["score"],om["score"]
    score=float(np.clip(.42*morphology_score+.30*ce+.22*oe+.06*spatial,0,1))
    if morphology_score>=.50 and ce>=.48 and oe>=.48 and spatial>=.10:
        result="Residue"
    elif morphology_score<.30 and ce<.55 and oe<.55:
        result="Non-residue"
    elif morphology_score>=.34 and min(ce,oe)>=.48 and spatial>=.07:
        result="Residue"
    else:
        result="Review"
    margin=abs(score-.50)
    conf="High" if result in {"Residue","Non-residue"} and margin>=.20 else "Medium" if result in {"Residue","Non-residue"} and margin>=.10 else "Low"
    reasons=[]
    if ce<.55 or oe<.55: reasons.append("weak_C_or_O_global_evidence")
    if abs(ce-oe)>.30: reasons.append("C_O_disagreement")
    if spatial<.08: reasons.append("weak_C_O_high_signal_overlap")
    candidate_area=int(cv2.countNonZero(chosen["mask"])); roi_area=max(int(cv2.countNonZero(roi)),1)
    return {
        "result":result,"confidence":conf,"residue_score":round(score,4),
        "c_enrichment":cm["contrast_pct"],"o_enrichment":om["contrast_pct"],"c_log2_ratio":cm["log2_ratio"],"o_log2_ratio":om["log2_ratio"],
        "c_zscore":cm["zscore"],"o_zscore":om["zscore"],"c_score":ce,"o_score":oe,
        "c_coverage":cm["coverage"],"o_coverage":om["coverage"],"c_spatial_overlap":cm["overlap"],"o_spatial_overlap":om["overlap"],
        "spatial_overlap":round(spatial*100,2),"n_enrichment":nm["contrast_pct"],"n_log2_ratio":nm["log2_ratio"],"n_zscore":nm["zscore"],"n_coverage":nm["coverage"],
        "si_enrichment":sm["contrast_pct"],"si_zscore":sm["zscore"],"morphology_score":round(morphology_score,4),"cluster_score":round(spatial,4),
        "roi_area_px":roi_area,"candidate_area_px":candidate_area,"candidate_coverage":round(candidate_area/roi_area*100,2),
        "sem_global_median":round(gmed,3),"sem_global_mad":round(gmad,3),"sem_candidate_zscore":round(morph_z,3),
        "c_roi_mean":cm["roi_mean"],"c_global_mean":cm["global_mean"],"c_global_std":cm["global_std"],
        "o_roi_mean":om["roi_mean"],"o_global_mean":om["global_mean"],"o_global_std":om["global_std"],
        "selected_candidate_rank":1,"candidate_count":len(scored),"candidate_selection_score":round(scored[0][0],4),
        "review_reasons":reasons,
        "n_note":"N is a diagnostic global-vs-ROI comparison and is not part of the current residue score.",
        "si_note":"Si is a diagnostic global-vs-ROI comparison and is not part of the current residue score.",
        "classification_note":"ROI and the whole analytical image are compared after excluding the bottom metadata/scale-bar region. The yellow Local Ring is visualization only and is never used as the classification baseline."
    },roi

def run(payload):
    outdir=Path(payload["output_dir"]); outdir.mkdir(parents=True,exist_ok=True); paths={}
    for local_no,info in enumerate(payload["pages"],1):
        with fitz.open(Path(info["path"])) as doc:
            page=doc.load_page(int(info["index"])); data=render_page_to_jpeg(page); out=outdir/f"page_{local_no}.jpg"; out.write_bytes(data); paths[f"page_{local_no}"]=str(out); del data,page
        gc.collect()
    first=cv2.imread(paths["page_1"],cv2.IMREAD_COLOR); second=cv2.imread(paths["page_2"],cv2.IMREAD_COLOR)
    if first is None or second is None: raise RuntimeError("Unable to read rendered source pages")
    p1=crop_page1_layout(first); p2=crop_page2_element_maps(second); del first,second; gc.collect()
    features,roi=residue_features(p1["sem"],p2["c_map"],p2["o_map"],p2["n_map"],p2["si_map"])
    raw={"sem":p1["sem"],"eds_map":p1["eds_map"],"full_element_maps_original":p2["full_element_maps_original"],"se_map":p2["se_map"],"c_map":p2["c_map"],"n_map":p2["n_map"],"o_map":p2["o_map"],"si_map":p2["si_map"]}
    for key,im in raw.items():
        fp=outdir/f"{key}.jpg"; save_crop(im,fp); paths[key]=str(fp)
    roi_sem=roi
    fp=outdir/"sem_residue_overlay.jpg"; save_crop(make_box_overlay(p1["sem"],roi_sem,roi_color=(0,0,255),ring_color=(0,220,255)),fp,92); paths["sem_residue_overlay"]=str(fp)
    fp=outdir/"sem_roi_ring_overlay.jpg"; save_crop(make_roi_ring_overlay(p1["sem"],roi_sem),fp,92); paths["sem_roi_ring_overlay"]=str(fp)
    fp=outdir/"element_maps_enhanced.jpg"; save_crop(make_element_overview(p2,roi),fp,92); paths["element_maps_enhanced"]=str(fp)
    for label,key in [("SE","se_map"),("C","c_map"),("N","n_map"),("O","o_map"),("Si","si_map")]:
        rr=cv2.resize(roi,(p2[key].shape[1],p2[key].shape[0]),interpolation=cv2.INTER_NEAREST)
        fp=outdir/f"{key}_roi_ring.jpg"; save_crop(make_roi_ring_overlay(p2[key],rr),fp,92); paths[f"{key}_roi_ring"]=str(fp)
        if label!="SE":
            enh=enhance_element_map(p2[key],label); fp=outdir/f"{key}_enhanced_overlay.jpg"; save_crop(make_box_overlay(enh,rr,roi_color=(255,255,255),ring_color=(0,220,255)),fp,92); paths[f"{key}_enhanced_overlay"]=str(fp)
    features.update({"map_parser":"Bruker page-2 SE/C/N/O/Si individual panels; ROI detected from Page-1 SEM","page":payload["page"],"cv_version":"v16-sem-roi-global-baseline","viewer_note":"ROI is detected from the Page-1 SEM per Point and projected by normalized coordinates. SEM uses a red rectangle; element maps use white rectangles; yellow line is visual-only and is not used for classification."})
    del roi,p1,p2,raw; gc.collect()
    rec={"id":payload["id"],"power":payload["power"],"time":payload["time"],"wafer":payload["wafer"],"point":payload["point"],"zone":payload["zone"],"condition":payload["condition"],"page":payload["page"],"pages_per_point":payload["pages_per_point"],"source_pages":payload["source_pages"],"assets":paths,"features":features}
    Path(payload["result_path"]).write_text(json.dumps(rec,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: python point_worker.py <point_input.json>")
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
