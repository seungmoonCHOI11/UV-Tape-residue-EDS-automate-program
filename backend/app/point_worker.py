"""Memory-minimal one-Point PDF/SEM/EDS worker for v15."""
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
    """Use the verified v13 Point-1 PDF crop boundaries.

    These are source-page pixel coordinates at the 1x PyMuPDF render used by
    this worker.  The v3 reference was template-matched back to the source PDF
    and all six reference crops matched at >0.99999.  Do not replace these with
    percentage-based crops: page margins vary less reliably than the actual
    Bruker panel boundaries.
    """
    h,w=img.shape[:2]
    # Reference page is 1240 x 1754. Scale only if a renderer ever returns a
    # different size; preserve the exact v3 coordinates at the normal size.
    sx,sy=w/1240.0,h/1754.0
    def box(x0,y0,x1,y1):
        return img[round(y0*sy):round(y1*sy),round(x0*sx):round(x1*sx)]
    return {
        "sem": box(98,207,98+628,207+418),
        "eds_map": box(101,818,101+1102,818+734),
    }


def crop_page2_element_maps(img):
    """Use the verified v13 Point-1 element-panel boundaries."""
    h,w=img.shape[:2]
    sx,sy=w/1240.0,h/1754.0
    def box(x0,y0,x1,y1):
        return img[round(y0*sy):round(y1*sy),round(x0*sx):round(x1*sx)]
    return {
        "full_element_maps_original": box(101,190,1200,1305),
        "se_map": box(101,190,643,552),
        "c_map": box(658,190,1200,552),
        "n_map": box(101,566,643,928),
        "o_map": box(658,566,1200,928),
        "si_map": box(101,943,643,1305),
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

def detect_residue_mask(se):
    """Return one conservative residue-candidate mask from the Page-2 SE map.

    The previous version kept the five largest components. That made unrelated
    bright artifacts (scale-bar/text/speckle) part of one ROI and could move the
    ROI far away from the visible residue.  The verified Point-1 reference shows
    one physical residue candidate, so production detection now selects the
    strongest *single* physical component in the analytical image area.
    """
    gray=cv2.cvtColor(se,cv2.COLOR_BGR2GRAY) if len(se.shape)==3 else se
    h,w=gray.shape
    analysis=gray[:int(h*0.84), :]
    clahe=cv2.createCLAHE(clipLimit=1.6,tileGridSize=(8,8)).apply(analysis)
    # Remove the broad SE background while preserving a particle-sized object.
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31))
    top=cv2.morphologyEx(clahe,cv2.MORPH_TOPHAT,kernel)
    top=cv2.GaussianBlur(top,(3,3),0)
    _,mask=cv2.threshold(top,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    n,labels,stats,cent=cv2.connectedComponentsWithStats(mask)
    total=h*w
    candidates=[]
    for i in range(1,n):
        x,y,ww,hh,area=stats[i]
        if area < max(40,int(total*0.00015)) or area > int(total*0.12):
            continue
        if x<=2 or x+ww>=w-2 or y<=2 or y+hh>=analysis.shape[0]-2:
            continue
        aspect=max(ww/max(hh,1),hh/max(ww,1))
        if aspect>12:
            continue
        # Prefer large, compact, high-contrast physical objects.
        fill=area/max(ww*hh,1)
        score=float(np.log1p(area)) + 1.8*fill
        candidates.append((score,int(area),i))
    out=np.zeros((h,w),np.uint8)
    if not candidates:
        return out
    _,_,best=max(candidates,key=lambda x:x[0])
    out[:analysis.shape[0],:][labels==best]=255
    return out

def roi_box_from_mask(mask, shape):
    """Convert the detected physical component into the viewer's rectangular ROI.

    v3 uses a rectangular ROI.  Padding is proportional to the detected object,
    so each Point moves with its actual residue instead of using one fixed page
    coordinate.  The box is clipped away from the SE metadata/scale-bar area.
    """
    h,w=shape[:2]
    ys,xs=np.where(mask>0)
    if len(xs)==0:
        # No defensible candidate: a small centered review box is preferable to
        # inventing a residue location. The classifier will mark this for review.
        bw,bh=max(40,int(w*.18)),max(35,int(h*.18))
        x0=(w-bw)//2; y0=min(max(10,(int(h*.38)-bh//2)),int(h*.78)-bh)
        x1=x0+bw; y1=y0+bh
    else:
        x0,x1=int(xs.min()),int(xs.max()+1); y0,y1=int(ys.min()),int(ys.max()+1)
        ww=max(x1-x0,12); hh=max(y1-y0,12)
        px=max(14,int(ww*.32)); py=max(12,int(hh*.30))
        x0-=px; x1+=px; y0-=py; y1+=py
        x0=max(4,x0); x1=min(w-4,x1); y0=max(4,y0); y1=min(int(h*.84),y1)
        bw,bh=x1-x0,y1-y0
        if bw<30: x1=min(w-4,x0+30)
        if bh<25: y1=min(int(h*.84),y0+25)
    box=np.zeros((h,w),np.uint8)
    cv2.rectangle(box,(x0,y0),(x1-1,y1-1),255,-1)
    return box

def local_element_metrics(element_img,roi,element):
    """Measure signal inside the rectangular ROI against a nearby local ring.

    Percent contrast is retained for diagnostics but is not used as the primary
    score because a near-zero background can turn a tiny absolute difference
    into misleading 200–300% numbers.  log2 ratio + local z-score are the main
    evidence metrics.
    """
    sig=resize_like(signal_channel(element_img,element),roi.shape).astype(np.float32)
    rb=roi>0
    if not np.any(rb):
        return {"contrast_pct":0,"log2_ratio":0,"zscore":0,"coverage":0,"overlap":0,"roi_mean":0,"bg_mean":0,"score":0}
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25))
    dil=cv2.dilate(rb.astype(np.uint8),kernel)>0
    ring=dil & (~rb)
    if int(ring.sum())<60:
        dil=cv2.dilate(rb.astype(np.uint8),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(41,41)))>0
        ring=dil & (~rb)
    inside=sig[rb]; bg=sig[ring]
    if inside.size==0 or bg.size==0:
        return {"contrast_pct":0,"log2_ratio":0,"zscore":0,"coverage":0,"overlap":0,"roi_mean":float(np.mean(inside)) if inside.size else 0,"bg_mean":float(np.mean(bg)) if bg.size else 0,"score":0}
    roi_mean=float(np.median(inside)); bg_mean=float(np.median(bg)); bg_std=float(np.std(bg)); eps=1.0
    ratio=(roi_mean+eps)/(bg_mean+eps)
    log2=float(np.log2(max(ratio,1e-6)))
    contrast=float((roi_mean-bg_mean)/(bg_mean+eps)*100)
    z=float((roi_mean-bg_mean)/max(bg_std,1.0))
    # Local high-signal overlap: signal must beat the local background, not a
    # global percentile that changes from one map to another.
    threshold=bg_mean+0.75*max(bg_std,1.0)
    high=sig>=threshold
    overlap=float(np.mean(high[rb])*100)
    # Log-ratio is deliberately capped before mapping to 0..1.
    score=float(1/(1+np.exp(-np.clip((log2-0.18)/0.35,-20,20))))
    return {
        "contrast_pct":round(float(np.clip(contrast,-100,100)),2),
        "log2_ratio":round(log2,4),
        "zscore":round(float(np.clip(z,-20,20)),3),
        "coverage":round(overlap,2),
        "overlap":round(overlap,2),
        "roi_mean":round(roi_mean,3),
        "bg_mean":round(bg_mean,3),
        "score":round(score,4),
    }

def sigmoid(x): return 1/(1+np.exp(-np.clip(x,-20,20)))

def make_box_overlay(base,roi,roi_color=(0,0,255),ring_color=(0,220,255)):
    """Draw the same rectangular ROI/ring style as the verified v3 reference."""
    out=base.copy()
    if len(out.shape)==2: out=cv2.cvtColor(out,cv2.COLOR_GRAY2BGR)
    ys,xs=np.where(roi>0)
    if len(xs)==0: return out
    x0,x1=int(xs.min()),int(xs.max()); y0,y1=int(ys.min()),int(ys.max())
    # Local ring is a viewer cue only: a thin dilation around the ROI.
    ring=cv2.dilate(roi,np.ones((21,21),np.uint8),iterations=1)
    ring=((ring>0)&(roi==0)).astype(np.uint8)*255
    contours,_=cv2.findContours(ring,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out,contours,-1,ring_color,2)
    cv2.rectangle(out,(x0,y0),(x1,y1),roi_color,3)
    return out

# Backward-compatible alias used by older report code.
def make_overlay(base,mask,title=None):
    return make_box_overlay(base,mask)

def make_roi_ring_overlay(base,roi,roi_color=(0,0,255),ring_color=(0,220,255)):
    return make_box_overlay(base,roi,roi_color,ring_color)

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

def residue_features(se_ref,c_map,o_map,n_map,si_map):
    candidate=detect_residue_mask(se_ref)
    roi=roi_box_from_mask(candidate,se_ref.shape)
    candidate_area=int(cv2.countNonZero(candidate)); roi_area=int(cv2.countNonZero(roi))
    candidate_coverage=candidate_area/max(roi_area,1)
    gray=cv2.cvtColor(se_ref,cv2.COLOR_BGR2GRAY)
    bg=cv2.GaussianBlur(gray,(0,0),7)
    contrast=cv2.absdiff(gray,bg)
    morph_contrast=float(np.mean(contrast[candidate>0]))/255 if candidate_area else 0
    # The v3 Point-1 residue occupies ~1–3% of the SE map and is strongly
    # brighter than its local background.  Use candidate coverage *inside the
    # ROI* plus local contrast, rather than ROI area alone.
    morphology_score=float(np.clip(.55*np.clip(candidate_coverage/0.42,0,1)+.45*np.clip(morph_contrast/0.18,0,1),0,1)) if candidate_area else 0.0
    cm=local_element_metrics(c_map,roi,"C")
    om=local_element_metrics(o_map,roi,"O")
    nm=local_element_metrics(n_map,roi,"N")
    sm=local_element_metrics(si_map,roi,"Si")
    ce=cm["score"]; oe=om["score"]
    spatial=float(np.clip((cm["overlap"]+om["overlap"])/200,0,1))
    score=float(np.clip(.45*morphology_score+.20*ce+.20*oe+.15*spatial,0,1))
    agreement=min(ce,oe)
    # Three-state output prevents a weak/contradictory signal from being called
    # residue merely because one element has a large relative percentage.
    if candidate_area==0:
        result="Review"
    elif morphology_score>=0.48 and ce>=0.62 and oe>=0.58 and spatial>=0.18:
        result="Residue"
    elif morphology_score<0.28 and ce<0.58 and oe<0.58:
        result="Non-residue"
    elif agreement>=0.62 and morphology_score>=0.36 and spatial>=0.12:
        result="Residue"
    else:
        result="Review"
    margin=abs(score-.50)
    conf="High" if result in {"Residue","Non-residue"} and margin>=.20 else "Medium" if result in {"Residue","Non-residue"} and margin>=.10 else "Low"
    reasons=[]
    if candidate_area==0: reasons.append("no_reliable_SE_candidate")
    if ce<0.55 or oe<0.55: reasons.append("weak_C_or_O_local_evidence")
    if abs(ce-oe)>0.30: reasons.append("C_O_disagreement")
    if spatial<0.12: reasons.append("weak_spatial_overlap")
    return {
        "result":result,"confidence":conf,"residue_score":round(score,4),
        "c_enrichment":cm["contrast_pct"],"o_enrichment":om["contrast_pct"],
        "c_log2_ratio":cm["log2_ratio"],"o_log2_ratio":om["log2_ratio"],
        "c_zscore":cm["zscore"],"o_zscore":om["zscore"],
        "c_score":cm["score"],"o_score":om["score"],
        "c_coverage":cm["coverage"],"o_coverage":om["coverage"],
        "c_spatial_overlap":cm["overlap"],"o_spatial_overlap":om["overlap"],
        "spatial_overlap":round(spatial*100,2),
        "n_enrichment":nm["contrast_pct"],"n_log2_ratio":nm["log2_ratio"],"n_zscore":nm["zscore"],"n_coverage":nm["coverage"],
        "si_enrichment":sm["contrast_pct"],"si_zscore":sm["zscore"],
        "morphology_score":round(morphology_score,4),"cluster_score":round(spatial,4),
        "roi_area_px":roi_area,"candidate_area_px":candidate_area,"candidate_coverage":round(candidate_area/max(roi_area,1)*100,2),
        "review_reasons":reasons,
        "n_note":"N is stored as a relative local signal and is not part of the current residue score.",
        "si_note":"Si is stored as a relative local signal and is not part of the current residue score.",
        "classification_note":"Residue requires agreement between a localized SE morphology candidate, local C/O evidence, and spatial overlap. Percent contrast is diagnostic only and is clipped; it is not wt%/at%.",
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
    features,roi=residue_features(p2["se_map"],p2["c_map"],p2["o_map"],p2["n_map"],p2["si_map"])
    raw={
        "sem":p1["sem"], "eds_map":p1["eds_map"],
        "full_element_maps_original":p2["full_element_maps_original"],
        "se_map":p2["se_map"], "c_map":p2["c_map"], "n_map":p2["n_map"],
        "o_map":p2["o_map"], "si_map":p2["si_map"]
    }
    for key,im in raw.items():
        fp=outdir/f"{key}.jpg"; save_crop(im,fp); paths[key]=str(fp)

    # Viewer-only overlays. The original Full Element Maps image above is
    # preserved exactly as a single crop; the derived enhanced overview is
    # separate and never replaces it.
    roi_sem=cv2.resize(roi,(p1["sem"].shape[1],p1["sem"].shape[0]),interpolation=cv2.INTER_NEAREST)
    fp=outdir/"sem_residue_overlay.jpg"
    save_crop(make_box_overlay(p1["sem"],roi_sem,roi_color=(0,0,255),ring_color=(0,220,255)),fp,92)
    paths["sem_residue_overlay"]=str(fp)

    fp=outdir/"sem_roi_ring_overlay.jpg"
    save_crop(make_roi_ring_overlay(p1["sem"],roi_sem),fp,92)
    paths["sem_roi_ring_overlay"]=str(fp)

    fp=outdir/"element_maps_enhanced.jpg"
    save_crop(make_element_overview(p2,roi),fp,92)
    paths["element_maps_enhanced"]=str(fp)

    # Individual maps: each file contains only its own element panel. The
    # same ROI and Local Ring are projected with identical normalized
    # coordinates. Enhanced overlays remain optional viewer assets.
    for label,key in [("SE","se_map"),("C","c_map"),("N","n_map"),("O","o_map"),("Si","si_map")]:
        rr=cv2.resize(roi,(p2[key].shape[1],p2[key].shape[0]),interpolation=cv2.INTER_NEAREST)
        fp=outdir/f"{key}_roi_ring.jpg"
        save_crop(make_roi_ring_overlay(p2[key],rr),fp,92)
        paths[f"{key}_roi_ring"]=str(fp)
        if label!="SE":
            enh=enhance_element_map(p2[key],label)
            fp=outdir/f"{key}_enhanced_overlay.jpg"
            save_crop(make_box_overlay(enh,rr,roi_color=(255,255,255),ring_color=(0,220,255)),fp,92)
            paths[f"{key}_enhanced_overlay"]=str(fp)
    features.update({"map_parser":"Bruker page-2 SE-referenced individual C/N/O/Si panels","page":payload["page"],"cv_version":"v15-verified-crop-local-roi","viewer_note":"ROI is detected from the SE map per Point and projected by normalized coordinates. SEM uses a red rectangle; element maps use white rectangles; yellow line marks the local comparison ring."})
    del roi; p1.clear(); p2.clear(); raw.clear(); gc.collect()
    rec={"id":payload["id"],"power":payload["power"],"time":payload["time"],"wafer":payload["wafer"],"point":payload["point"],"zone":payload["zone"],"condition":payload["condition"],"page":payload["page"],"pages_per_point":payload["pages_per_point"],"source_pages":payload["source_pages"],"assets":paths,"features":features}
    Path(payload["result_path"]).write_text(json.dumps(rec,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: python point_worker.py <point_input.json>")
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
