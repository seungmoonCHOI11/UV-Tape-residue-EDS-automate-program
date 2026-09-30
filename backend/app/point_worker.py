"""Memory-minimal one-Point PDF/SEM/EDS worker for v16.

v16 changes:
- keeps the verified v13 Point-1 crop boundaries;
- detects the ROI from the higher-resolution Page-1 SEM crop, not the Page-2 SE crop;
- excludes the SEM footer/scale-bar region from detection and global statistics;
- compares ROI signal with the whole analytical image (global robust baseline), not a local ring;
- removes the yellow Local Ring entirely; global image statistics are the only baseline;
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
    # Direct signed local-background segmentation captures the full body of large
    # residues that top-hat can otherwise fragment into only a bright edge.
    raw=work.astype(np.float32)
    raw_bg=cv2.GaussianBlur(raw,(0,0),17)
    delta=raw-raw_bg
    dmed=float(np.median(delta)); dmad=float(np.median(np.abs(delta-dmed))); drs=max(1.0,1.4826*dmad)
    for polarity in (1,-1):
        signed=delta*polarity
        threshold=max(7.0,2.45*drs)
        m=(signed>=threshold).astype(np.uint8)*255
        m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9)))
        m=cv2.morphologyEx(m,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
        cues.append(m)
    for k in (21,31,45):
        kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k))
        top=cv2.morphologyEx(clahe,cv2.MORPH_TOPHAT,kernel)
        top=cv2.GaussianBlur(top,(3,3),0)
        _,m=cv2.threshold(top,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        m=cv2.morphologyEx(m,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
        cues.append(m)
    # Keep the original bright-residue detector, but add a separate dark-contrast
    # branch so large/low-brightness residues are not silently lost.  The branches
    # are merged only at candidate generation; morphology + C/O evidence still
    # decides which candidates are physically plausible.
    for k in (31,45,61):
        kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k))
        black=cv2.morphologyEx(clahe,cv2.MORPH_BLACKHAT,kernel)
        black=cv2.GaussianBlur(black,(3,3),0)
        _,m=cv2.threshold(black,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        m=cv2.morphologyEx(m,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
        cues.append(m)
    # Keep direct signed-background components separate from top-hat/black-hat
    # components so a large residue is not merged into a page-wide background blob.
    direct_cues=cues[:2]
    mask=cues[2]
    for extra in cues[3:]: mask=cv2.bitwise_or(mask,extra)
    n,labels,stats,_=cv2.connectedComponentsWithStats(mask)
    background=cv2.GaussianBlur(clahe,(0,0),17)
    edge_map=cv2.Canny(clahe,50,120)
    total=float(work.shape[0]*work.shape[1]); cand=[]
    for i in range(1,n):
        x,y,ww,hh,area=stats[i]
        if area < max(35,int(total*0.00008)) or area > int(total*0.20): continue
        if x<=3 or y<=3 or x+ww>=w-3 or y+hh>=int(h*.84)-3: continue
        aspect=max(ww/max(hh,1),hh/max(ww,1))
        # Reject page-border/footer structures and extremely page-spanning components.
        if aspect>18 or ww>int(w*.72) or hh>int(h*.58): continue
        ys,xs=np.where(labels==i); vals=clahe[ys,xs].astype(np.float32); bgv=background[ys,xs].astype(np.float32)
        contrast=float(np.median(vals-bgv))/max(float(np.std(background)),8.0)
        fill=area/max(ww*hh,1); edge=float(np.mean(edge_map[ys,xs]>0))
        area_term=np.clip(np.log1p(area)/9.0,0,1); fill_term=np.clip(fill/.55,0,1); contrast_term=np.clip((contrast+.3)/3.5,0,1); edge_term=np.clip(edge/.35,0,1)
        morph_rank=.48*area_term+.27*contrast_term+.15*fill_term+.10*edge_term
        cm=np.zeros_like(gray,np.uint8); cm[ys,xs]=255
        cand.append({"label":i,"score":float(morph_rank),"area":int(area),"bbox":(int(x),int(y),int(ww),int(hh)),"mask":cm})
    # Add direct signed-background components separately. These are especially important
    # for large bright/dark residues whose interior is not a top-hat edge.
    for dc in direct_cues:
        n2,l2,st2,_=cv2.connectedComponentsWithStats(dc)
        for i in range(1,n2):
            x,y,ww,hh,area=[int(v) for v in st2[i]]
            if area < max(35,int(total*0.00008)) or area > int(total*0.20): continue
            if x<=3 or y<=3 or x+ww>=w-3 or y+hh>=int(h*.84)-3: continue
            aspect=max(ww/max(hh,1),hh/max(ww,1))
            if aspect>18 or ww>int(w*.72) or hh>int(h*.58): continue
            ys,xs=np.where(l2==i); vals=clahe[ys,xs].astype(np.float32); bgv=background[ys,xs].astype(np.float32)
            contrast=float(np.median(vals-bgv))/max(float(np.std(background)),8.0)
            fill=area/max(ww*hh,1); edge=float(np.mean(edge_map[ys,xs]>0))
            area_term=np.clip(np.log1p(area)/9.0,0,1); fill_term=np.clip(fill/.55,0,1); contrast_term=np.clip((abs(contrast)+.3)/3.5,0,1); edge_term=np.clip(edge/.35,0,1)
            morph_rank=.40*area_term+.34*contrast_term+.18*fill_term+.08*edge_term
            cm=np.zeros_like(gray,np.uint8); cm[ys,xs]=255
            cand.append({"label":20000+i,"score":float(morph_rank),"area":int(area),"bbox":(int(x),int(y),int(ww),int(hh)),"mask":cm,"direct_background":True})
    cand.sort(key=lambda z:z["score"],reverse=True)
    # Merge fragments that are close enough to plausibly belong to the same physical
    # residue. This fixes large/irregular residues being split into tiny candidates.
    merged=[]
    for c in cand:
        placed=False
        x,y,ww,hh=c["bbox"]
        for d in merged:
            dx,dy,dw,dh=d["bbox"]
            gap_x=max(dx-x, x-(dx+dw), 0)
            gap_y=max(dy-y, y-(dy+dh), 0)
            near_limit=max(6, int(min(max(ww,hh),max(dw,dh))*0.16))
            center_dist=np.hypot((x+ww/2)-(dx+dw/2),(y+hh/2)-(dy+dh/2))
            size_ref=max(min(ww,hh,dw,dh),8)
            if (gap_x<=near_limit and gap_y<=near_limit) or center_dist<=max(near_limit*2.2,size_ref*1.15):
                union=cv2.bitwise_or(d["mask"],c["mask"])
                # A moderate closing connects a broken physical contour without
                # turning a distant residue into a giant rectangle.
                k=max(5,min(17,int(round(min(max(ww,hh),max(dw,dh))*0.10))|1))
                union=cv2.morphologyEx(union,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k)))
                ys2,xs2=np.where(union>0)
                if len(xs2):
                    bx=(int(xs2.min()),int(ys2.min()),int(xs2.max()-xs2.min()+1),int(ys2.max()-ys2.min()+1))
                    d["mask"]=union; d["bbox"]=bx; d["area"]=int(cv2.countNonZero(union)); d["score"]=max(d["score"],c["score"]); d["merged_fragments"]=d.get("merged_fragments",1)+1
                placed=True; break
        if not placed:
            merged.append({**c,"merged_fragments":1})
    out=[]
    for c in sorted(merged,key=lambda z:z["score"],reverse=True):
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


def make_box_overlay(base,roi,roi_color=(0,0,255),ring_color=None):
    """Draw only the actual irregular ROI contour; never draw a yellow ring/box."""
    out=base.copy()
    if out.ndim==2: out=cv2.cvtColor(out,cv2.COLOR_GRAY2BGR)
    mask=(roi>0).astype(np.uint8)
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(out,contours,-1,roi_color,3)
    return out

def make_overlay(base,mask,title=None): return make_box_overlay(base,mask)
def make_roi_ring_overlay(base,roi,roi_color=(0,0,255),ring_color=None): return make_box_overlay(base,roi,roi_color,None)

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



def _ai_guided_mask(sem, boxes):
    """Turn coarse AI boxes into smooth local CV masks; boxes themselves are never the ROI."""
    if not boxes:
        return np.zeros(sem.shape[:2], np.uint8)
    gray=cv2.cvtColor(sem,cv2.COLOR_BGR2GRAY) if sem.ndim==3 else sem.copy()
    h,w=gray.shape; out=np.zeros((h,w),np.uint8); work_h=int(h*.84)
    clahe=cv2.createCLAHE(clipLimit=1.5,tileGridSize=(8,8)).apply(gray[:work_h,:])
    for b in boxes:
        try:
            x=int(float(b.get("x",0))*w); y=int(float(b.get("y",0))*h)
            x2=min(w,int((float(b.get("x",0))+float(b.get("w",0)))*w))
            y2=min(work_h,int((float(b.get("y",0))+float(b.get("h",0)))*h))
        except Exception:
            continue
        x=max(4,min(w-5,x)); y=max(4,min(work_h-5,y)); x2=max(x+8,min(w-4,x2)); y2=max(y+8,min(work_h-4,y2))
        cw,ch=x2-x,y2-y
        if cw<10 or ch<10: continue
        crop=clahe[y:y2,x:x2]
        # Multi-scale local contrast. This handles bright, dark and faint residues
        # on a slowly varying SEM background without a single global threshold.
        cues=[]
        for k in (15,25,35):
            ker=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k))
            th=cv2.morphologyEx(crop,cv2.MORPH_TOPHAT,ker)
            bh=cv2.morphologyEx(crop,cv2.MORPH_BLACKHAT,ker)
            for cue in (th,bh):
                cue=cv2.GaussianBlur(cue,(3,3),0)
                _,m=cv2.threshold(cue,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
                cues.append(m)
        mask=np.zeros_like(crop,np.uint8)
        for m in cues: mask=cv2.bitwise_or(mask,m)
        # Smooth speckles into physical-looking connected shapes.
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9)),iterations=1)
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)),iterations=1)
        n,lab,stats,_=cv2.connectedComponentsWithStats(mask)
        keep=np.zeros_like(mask)
        min_area=max(8,int(cw*ch*0.002)); max_area=max(min_area+1,int(cw*ch*0.72))
        for i in range(1,n):
            area=int(stats[i,cv2.CC_STAT_AREA])
            ww=int(stats[i,cv2.CC_STAT_WIDTH]); hh=int(stats[i,cv2.CC_STAT_HEIGHT])
            if min_area<=area<=max_area and max(ww/max(hh,1),hh/max(ww,1))<18:
                keep[lab==i]=255
        # A very faint object can disappear from the binary mask. In that case use
        # the strongest smooth connected component rather than filling the whole AI box.
        if cv2.countNonZero(keep)==0 and cv2.countNonZero(mask)>0:
            n,lab,stats,_=cv2.connectedComponentsWithStats(mask)
            best=max(range(1,n), key=lambda i:int(stats[i,cv2.CC_STAT_AREA]), default=0)
            if best: keep[lab==best]=255
        full=np.zeros((h,w),np.uint8); full[y:y2,x:x2]=keep
        out=cv2.bitwise_or(out,full)
    # Final smoothing and tiny-speckle removal.
    out=cv2.morphologyEx(out,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)),iterations=1)
    n,lab,stats,_=cv2.connectedComponentsWithStats(out)
    clean=np.zeros_like(out)
    min_total=max(12,int(np.count_nonzero(out)*0.003))
    for i in range(1,n):
        if int(stats[i,cv2.CC_STAT_AREA])>=min_total: clean[lab==i]=255
    return clean

def refine_candidate_mask(sem_ref, candidate_mask):
    """Refine a candidate into the physical residue mask while rejecting broad shadow."""
    gray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY).astype(np.float32)
    am=analytical_mask(gray.shape)>0
    cand=(candidate_mask>0)&am
    if not np.any(cand): return np.zeros_like(candidate_mask)
    bg=cv2.GaussianBlur(gray,(0,0),17)
    signed=gray-bg
    vals=signed[cand]
    if vals.size<20: return candidate_mask.copy()
    # Determine whether this candidate is physically bright or dark relative to its
    # smoothly varying background. Shadow usually has the opposite polarity and is
    # therefore removed instead of being absorbed into the residue mask.
    polarity=1.0 if float(np.median(vals))>=0 else -1.0
    signal=signed*polarity
    sigvals=signal[cand]
    # Robust threshold: keep the stronger half of the candidate's signed contrast,
    # but do not require an extreme contrast for dark/faint residues.
    p35=float(np.percentile(sigvals,35)); p55=float(np.percentile(sigvals,55))
    spread=float(np.median(np.abs(sigvals-np.median(sigvals))))*1.4826
    threshold=max(2.0,p35*0.72,p55-spread*0.55)
    core=((cand)&(signal>=threshold)).astype(np.uint8)*255
    core=cv2.morphologyEx(core,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9)))
    core=cv2.morphologyEx(core,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
    # Controlled fill of narrow gaps within the same physical object.
    n,lab,stats,_=cv2.connectedComponentsWithStats(core)
    if n>1:
        best=max(range(1,n),key=lambda i:int(stats[i,cv2.CC_STAT_AREA]))
        core=(lab==best).astype(np.uint8)*255
    # Recover adjacent residue edge pixels of the same polarity, but never pixels
    # whose signed contrast has crossed into the shadow/background side.
    grown=cv2.dilate(core,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)),iterations=1)
    edge_threshold=max(0.8,threshold*0.42)
    refined=((grown>0)&cand&(signal>=edge_threshold)).astype(np.uint8)*255
    refined=cv2.morphologyEx(refined,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)))
    refined=cv2.morphologyEx(refined,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
    if cv2.countNonZero(refined)<max(20,int(cv2.countNonZero(core)*0.45)):
        refined=core
    return refined


def roi_quality_metrics(sem_ref, roi, selected_count=1):
    """Estimate whether the irregular mask plausibly covers a physical residue.

    This is a QC metric, not a substitute for the residue score. It penalizes tiny
    fragments and masks that occupy only a small sliver of their own bounding box.
    """
    gray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY)
    am=analytical_mask(gray.shape)>0
    mask=(roi>0)&am
    ys,xs=np.where(mask)
    if len(xs)==0:
        return {"roi_quality":0.0,"roi_fill_ratio":0.0,"roi_component_count":0,"roi_bbox_area_px":0}
    x0,x1=int(xs.min()),int(xs.max()+1); y0,y1=int(ys.min()),int(ys.max()+1)
    bbox=max(1,(x1-x0)*(y1-y0)); area=int(mask.sum())
    fill=area/bbox
    n,lab,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    comps=max(0,n-1)
    # A single coherent contour is preferable; multiple components are allowed but
    # reduce confidence because they can indicate a split residue.
    coherence=1.0 if comps<=1 else float(np.clip(1.0-0.14*(comps-1),0.45,1.0))
    grad=cv2.Laplacian(gray,cv2.CV_32F).var()
    contour_strength=0.0
    contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        edges=cv2.Canny(gray,40,110)>0
        contour_px=np.zeros_like(mask,dtype=np.uint8)
        cv2.drawContours(contour_px,contours,-1,1,1)
        contour_strength=float(np.mean(edges[contour_px>0])) if np.any(contour_px) else 0.0
    edge_term=float(np.clip(contour_strength/0.45,0,1))
    q=float(np.clip(0.45*np.clip(fill/0.35,0,1)+0.35*coherence+0.20*edge_term,0,1))
    return {"roi_quality":round(q,4),"roi_fill_ratio":round(fill,4),"roi_component_count":comps,"roi_bbox_area_px":bbox}

def residue_features(sem_ref,c_map,o_map,n_map,si_map,ai_boxes=None):
    """Detect one or more physical residue regions and compare them with the whole field.

    The analysis ROI is an irregular pixel mask for the primary physical residue. Nearby
    fragments are merged before selection, while separate particles are not allowed to
    dilute the primary ROI. Broad SEM shadow is filtered before the final mask is scored.
    """
    candidates=detect_residue_candidates(sem_ref,8)
    ai_mask=_ai_guided_mask(sem_ref, ai_boxes)
    if cv2.countNonZero(ai_mask):
        # Prefer existing CV candidates that intersect an AI-proposed region, but also
        # keep a new smooth local segmentation when CV missed a large/faint object.
        for c in candidates:
            overlap=cv2.countNonZero(cv2.bitwise_and(c["mask"],ai_mask)) / max(cv2.countNonZero(c["mask"]),1)
            c["ai_overlap"]=float(overlap)
            if overlap>0.10: c["score"]=min(1.0,c["score"]+0.10*overlap)
        n,lab,stats,_=cv2.connectedComponentsWithStats(ai_mask)
        for i in range(1,n):
            area=int(stats[i,cv2.CC_STAT_AREA]); x,y,ww,hh=[int(stats[i,j]) for j in range(4)]
            if area<20: continue
            cm=np.zeros_like(ai_mask,np.uint8); cm[lab==i]=255
            candidates.append({"label":10000+i,"score":0.62,"area":area,"bbox":(x,y,ww,hh),"mask":cm,"cx":x+ww/2,"cy":y+hh/2,"ww":ww,"hh":hh,"ai_guided":True})
        # Keep candidates compact and spatially distinct again.
        ded=[]
        for c in sorted(candidates,key=lambda z:z.get("score",0),reverse=True):
            x,y,ww,hh=c["bbox"]; cx=x+ww/2; cy=y+hh/2
            if any(np.hypot((cx-d["cx"])/max(d["ww"],20),(cy-d["cy"])/max(d["hh"],20))<.45 for d in ded): continue
            ded.append(c)
            if len(ded)>=10: break
        candidates=ded
    # Refine each candidate before scoring so broad shadow regions do not become the ROI.
    for c in candidates:
        c["mask"]=refine_candidate_mask(sem_ref,c["mask"])
        ys,xs=np.where(c["mask"]>0)
        if len(xs):
            c["bbox"]=(int(xs.min()),int(ys.min()),int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1))
            c["area"]=int(len(xs)); c["cx"]=float((xs.min()+xs.max())/2); c["cy"]=float((ys.min()+ys.max())/2); c["ww"]=float(c["bbox"][2]); c["hh"]=float(c["bbox"][3])
    candidates=[c for c in candidates if cv2.countNonZero(c["mask"])>=20]
    h,w=sem_ref.shape[:2]
    gray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY)
    am=analytical_mask(gray.shape)>0
    gmed=float(np.median(gray[am])); gmad=float(np.median(np.abs(gray[am]-gmed))); gstd=max(1.4826*gmad,1.0)
    scored=[]
    for c in candidates:
        # Candidate pixels themselves are used for the signal comparison; no local ring.
        cmask=c["mask"]
        roi_c=cv2.resize(cmask,(c_map.shape[1],c_map.shape[0]),interpolation=cv2.INTER_NEAREST)
        roi_o=cv2.resize(cmask,(o_map.shape[1],o_map.shape[0]),interpolation=cv2.INTER_NEAREST)
        cm=global_roi_metrics(c_map,roi_c,"C"); om=global_roi_metrics(o_map,roi_o,"O")
        candidate_area=int(cv2.countNonZero(cmask)); vals=gray[cmask>0].astype(np.float32)
        morph_z=(float(np.median(vals))-gmed)/gstd if vals.size else 0.0
        # Absolute local contrast allows both bright and dark residues, while area/fill
        # prevents isolated background grain from becoming a high-ranked candidate.
        local_bg=cv2.GaussianBlur(gray,(0,0),17)
        local_contrast=float(np.median(np.abs(vals-local_bg[cmask>0]))) if vals.size else 0.0
        area_fraction=candidate_area/max(int(np.count_nonzero(am)),1)
        morphology_score=float(np.clip(
            .42*sigmoid((abs(morph_z)-.65)/1.25)+
            .28*np.clip(c["score"],0,1)+
            .18*np.clip(local_contrast/18.0,0,1)+
            .12*np.clip(np.sqrt(area_fraction)*16,0,1),0,1))
        spatial=float(np.clip(min(cm["coverage"],om["coverage"])/55.0,0,1))
        ai_bonus=0.08 if c.get("ai_guided") else 0.0
        selection=float(np.clip(.34*morphology_score+.28*cm["score"]+.26*om["score"]+.07*spatial+ai_bonus,0,1))
        scored.append((selection,c,cm,om,morphology_score,morph_z,spatial))
    if not scored:
        roi=np.zeros((h,w),np.uint8)
        return {"result":"Review","confidence":"Low","residue_score":0.0,"c_enrichment":0.0,"o_enrichment":0.0,
                "c_log2_ratio":0.0,"o_log2_ratio":0.0,"c_zscore":0.0,"o_zscore":0.0,"c_score":0.0,"o_score":0.0,
                "c_coverage":0.0,"o_coverage":0.0,"c_spatial_overlap":0.0,"o_spatial_overlap":0.0,"spatial_overlap":0.0,
                "morphology_score":0.0,"cluster_score":0.0,"roi_area_px":0,"candidate_area_px":0,"candidate_coverage":0.0,
                "review_reasons":["no_reliable_SEM_candidate"],"classification_note":"No defensible SEM candidate was detected; point requires human review."},roi

    scored.sort(key=lambda z:z[0],reverse=True)
    best_score=scored[0][0]
    # Select one primary physical residue. Fragmented pieces belonging to that residue
    # are merged earlier; separate distant particles should not dilute the main ROI.
    item=scored[0]
    selected=[{"sel":item[0],"c":item[1],"cm":item[2],"om":item[3],"ms":item[4],"mz":item[5],"sp":item[6],"cx":item[1]["cx"],"cy":item[1]["cy"],"ww":item[1]["ww"],"hh":item[1]["hh"]}]

    roi=np.zeros((h,w),np.uint8)
    for d in selected: roi=cv2.bitwise_or(roi,d["c"]["mask"])
    # Recalculate element metrics on the final irregular ROI.
    rr_c=cv2.resize(roi,(c_map.shape[1],c_map.shape[0]),interpolation=cv2.INTER_NEAREST)
    rr_o=cv2.resize(roi,(o_map.shape[1],o_map.shape[0]),interpolation=cv2.INTER_NEAREST)
    rr_n=cv2.resize(roi,(n_map.shape[1],n_map.shape[0]),interpolation=cv2.INTER_NEAREST)
    rr_si=cv2.resize(roi,(si_map.shape[1],si_map.shape[0]),interpolation=cv2.INTER_NEAREST)
    cm=global_roi_metrics(c_map,rr_c,"C"); om=global_roi_metrics(o_map,rr_o,"O")
    nm=global_roi_metrics(n_map,rr_n,"N"); sm=global_roi_metrics(si_map,rr_si,"Si")
    ce,oe=cm["score"],om["score"]
    morph=np.mean([d["ms"] for d in selected])
    spatial=float(np.clip(min(cm["coverage"],om["coverage"])/55.0,0,1))
    # v19 score emphasizes physically visible SEM morphology while still requiring
    # elemental support. A strong, coherent SEM residue with at least one supporting
    # C/O channel receives a bounded visual-evidence bonus; weak/noisy candidates do not.
    element_max=max(ce,oe); element_min=min(ce,oe)
    rq=roi_quality_metrics(sem_ref,roi,len(selected))
    visual_bonus=0.15 if (morph>=0.85 and rq["roi_quality"]>=0.70 and element_max>=0.45) else 0.0
    raw_score=float(np.clip(.65*morph+.20*element_max+.10*element_min+.05*spatial+visual_bonus,0,1))
    # Score calibration requested by the lab workflow:
    # - keep the displayed score on a true 0~100 scale
    # - map the previous 85-point level to the new 70-point level
    # - keep 0 -> 0 and 100 -> 100
    # - do NOT use a flat -15 point offset (that would cap the maximum at 85)
    #
    # Below 85, compress the score slightly so the old 70~85 band does not become
    # overly generous. Above 85, expand the upper band so genuinely strong points
    # can still reach 100.
    if raw_score <= 0.85:
        score=float(np.clip(raw_score*(0.70/0.85),0,0.70))
    else:
        score=float(np.clip(0.70+(raw_score-0.85)*(0.30/0.15),0.70,1.0))
    residue_gate=(score>=0.70 and rq["roi_quality"]>=0.55 and (element_max>=0.45 or morph>=0.90))
    non_gate=(score<0.40 and morph<0.34 and element_max<0.55)
    if residue_gate: result="Residue"
    elif non_gate: result="Non-residue"
    else: result="Review"
    margin=abs(score-0.70 if result=="Residue" else score-0.40)
    conf="High" if result in {"Residue","Non-residue"} and margin>=.20 else "Medium" if result in {"Residue","Non-residue"} and margin>=.10 else "Low"
    reasons=[]
    if ce<.55 or oe<.55: reasons.append("weak_C_or_O_global_evidence")
    if abs(ce-oe)>.30: reasons.append("C_O_disagreement")
    if spatial<.08: reasons.append("weak_C_O_high_signal_overlap")
    if rq["roi_quality"]<.55: reasons.append("low_roi_quality_or_partial_mask")
    if score>=.70 and rq["roi_quality"]<.55: reasons.append("high_score_but_roi_quality_low")
    roi_area=int(cv2.countNonZero(roi)); candidate_area=sum(int(cv2.countNonZero(d["c"]["mask"])) for d in selected)
    ys,xs=np.where(roi>0); box_area=0 if len(xs)==0 else max(1,(xs.max()-xs.min()+1)*(ys.max()-ys.min()+1))
    return {
        "result":result,"confidence":conf,"residue_score":round(score,4),"raw_residue_score":round(raw_score,4),"score_calibration_method":"piecewise_0_to_85_to_70_100_preserved","score_calibration_anchor_previous_85_new_70":True,"visual_evidence_bonus":round(float(visual_bonus),4),
        "c_enrichment":cm["contrast_pct"],"o_enrichment":om["contrast_pct"],"c_log2_ratio":cm["log2_ratio"],"o_log2_ratio":om["log2_ratio"],
        "c_zscore":cm["zscore"],"o_zscore":om["zscore"],"c_score":ce,"o_score":oe,
        "c_coverage":cm["coverage"],"o_coverage":om["coverage"],"c_spatial_overlap":cm["overlap"],"o_spatial_overlap":om["overlap"],
        "spatial_overlap":round(spatial*100,2),"n_enrichment":nm["contrast_pct"],"n_log2_ratio":nm["log2_ratio"],"n_zscore":nm["zscore"],"n_coverage":nm["coverage"],
        "si_enrichment":sm["contrast_pct"],"si_zscore":sm["zscore"],"morphology_score":round(float(morph),4),"cluster_score":round(spatial,4),
        "roi_area_px":roi_area,"candidate_area_px":candidate_area,"candidate_coverage":round(candidate_area/max(box_area,1)*100,2),
        "roi_quality":rq["roi_quality"],"roi_fill_ratio":rq["roi_fill_ratio"],"roi_component_count":rq["roi_component_count"],"roi_bbox_area_px":rq["roi_bbox_area_px"],
        "sem_global_median":round(gmed,3),"sem_global_mad":round(gmad,3),"sem_candidate_zscore":round(float(np.mean([d["mz"] for d in selected])),3),
        "c_roi_mean":cm["roi_mean"],"c_global_mean":cm["global_mean"],"c_global_std":cm["global_std"],
        "o_roi_mean":om["roi_mean"],"o_global_mean":om["global_mean"],"o_global_std":om["global_std"],
        "selected_candidate_rank":1,"candidate_count":len(scored),"selected_candidate_count":len(selected),"candidate_selection_score":round(best_score,4),
        "review_reasons":reasons,
        "n_note":"N is a diagnostic global-vs-ROI comparison and is not part of the current residue score.",
        "si_note":"Si is a diagnostic global-vs-ROI comparison and is not part of the current residue score.",
        "classification_note":"The irregular ROI mask is compared with the whole analytical image after excluding the bottom metadata/scale-bar region. The Local Ring is removed and is not used as a baseline. The displayed score remains a true 0-100 score: previous 0 maps to 0, previous 85 maps to 70, and previous 100 maps to 100 using a piecewise calibration. Low ROI quality forces REVIEW even when the signal score is high."
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
    features,roi=residue_features(p1["sem"],p2["c_map"],p2["o_map"],p2["n_map"],p2["si_map"],payload.get("ai_roi_boxes"))
    raw={"sem":p1["sem"],"eds_map":p1["eds_map"],"full_element_maps_original":p2["full_element_maps_original"],"se_map":p2["se_map"],"c_map":p2["c_map"],"n_map":p2["n_map"],"o_map":p2["o_map"],"si_map":p2["si_map"]}
    for key,im in raw.items():
        fp=outdir/f"{key}.jpg"; save_crop(im,fp); paths[key]=str(fp)
    roi_sem=roi
    fp=outdir/"sem_residue_overlay.jpg"; save_crop(make_box_overlay(p1["sem"],roi_sem,roi_color=(0,0,255),ring_color=(0,220,255)),fp,92); paths["sem_residue_overlay"]=str(fp)
    # Backward-compatible asset name: it now contains the contour-only overlay.
    fp=outdir/"sem_roi_ring_overlay.jpg"; save_crop(make_box_overlay(p1["sem"],roi_sem,roi_color=(0,0,255)),fp,92); paths["sem_roi_ring_overlay"]=str(fp)
    fp=outdir/"element_maps_enhanced.jpg"; save_crop(make_element_overview(p2,roi),fp,92); paths["element_maps_enhanced"]=str(fp)
    for label,key in [("SE","se_map"),("C","c_map"),("N","n_map"),("O","o_map"),("Si","si_map")]:
        rr=cv2.resize(roi,(p2[key].shape[1],p2[key].shape[0]),interpolation=cv2.INTER_NEAREST)
        # Backward-compatible asset name; yellow ring is no longer drawn.
        fp=outdir/f"{key}_roi_ring.jpg"; save_crop(make_box_overlay(p2[key],rr,roi_color=(255,255,255)),fp,92); paths[f"{key}_roi_ring"]=str(fp)
        if label!="SE":
            enh=enhance_element_map(p2[key],label); fp=outdir/f"{key}_enhanced_overlay.jpg"; save_crop(make_box_overlay(enh,rr,roi_color=(255,255,255)),fp,92); paths[f"{key}_enhanced_overlay"]=str(fp)
    features.update({"ai_roi_guided": bool(payload.get("ai_roi_boxes")), "ai_roi_box_count": len(payload.get("ai_roi_boxes") or []),"map_parser":"Bruker page-2 SE/C/N/O/Si individual panels; ROI detected from Page-1 SEM","page":payload["page"],"cv_version":"v20-irregular-mask-merge-shadow-filter-piecewise-score-calibrated","viewer_note":"ROI is detected from the Page-1 SEM as an irregular residue mask, with nearby fragments merged and shadow/background filtered. Red/white contours show the actual ROI. No Local Ring is displayed or used. Score remains 0-100 with piecewise calibration: previous 85 -> new 70 and previous 100 -> new 100."})
    del roi,p1,p2,raw; gc.collect()
    rec={"id":payload["id"],"power":payload["power"],"time":payload["time"],"wafer":payload["wafer"],"point":payload["point"],"zone":payload["zone"],"condition":payload["condition"],"page":payload["page"],"pages_per_point":payload["pages_per_point"],"source_pages":payload["source_pages"],"assets":paths,"features":features}
    Path(payload["result_path"]).write_text(json.dumps(rec,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: python point_worker.py <point_input.json>")
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
