"""Memory-minimal one-Point PDF/SEM/EDS worker for v12."""
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
    h,w=img.shape[:2]
    return {
        # Keep the SEM information block below the micrograph; the previous crop
        # ended at 40% of the page and cut the Name/Date/HV/Mag/WD information.
        "sem":img[int(.08*h):int(.52*h),int(.05*w):int(.62*w)],
        # Preserve the full EDS map width and its lower legend/scale information.
        "eds_map":img[int(.43*h):int(.96*h),int(.04*w):int(.99*w)]
    }

def crop_page2_element_maps(img):
    h,w=img.shape[:2]
    x0,xmid,x1=int(.06*w),int(.50*w),int(.96*w); y0,y1,y2,y3=int(.09*h),int(.31*h),int(.55*h),int(.79*h)
    return {"se_map":img[y0:y1,x0:xmid],"c_map":img[y0:y1,xmid:x1],"n_map":img[y1:y2,x0:xmid],"o_map":img[y1:y2,xmid:x1],"si_map":img[y2:y3,x0:xmid]}

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
    """Detect meaningful bright residue candidates from the Page-2 SE reference.

    A white top-hat is used instead of a global/adaptive threshold so the
    fine EDS/SE speckle background does not become thousands of tiny ROIs.
    The largest few meaningful connected components are retained.
    """
    gray=cv2.cvtColor(se,cv2.COLOR_BGR2GRAY) if len(se.shape)==3 else se
    clahe=cv2.createCLAHE(clipLimit=1.8,tileGridSize=(8,8)).apply(gray)
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31))
    top=cv2.morphologyEx(clahe,cv2.MORPH_TOPHAT,kernel)
    top=cv2.GaussianBlur(top,(3,3),0)
    _,mask=cv2.threshold(top,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
    h,w=mask.shape[:2]; total=h*w; n,labels,stats,_=cv2.connectedComponentsWithStats(mask); out=np.zeros_like(mask)
    min_area=max(30,int(total*0.00030)); max_area=int(total*0.20)
    candidates=[]
    for i in range(1,n):
        x,y,ww,hh,area=stats[i]
        if area<min_area or area>max_area or x<=1 or y<=1 or x+ww>=w-1 or y+hh>=h-1: continue
        aspect=max(ww/max(hh,1),hh/max(ww,1))
        if aspect>35 and area<total*0.002: continue
        candidates.append((int(area),i))
    # Keep up to five strongest physical candidates, which also handles multiple
    # residue fragments without admitting the fine speckle field.
    for area,i in sorted(candidates,reverse=True)[:5]: out[labels==i]=255
    if cv2.countNonZero(out)==0 and n>1:
        idx=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA])); out[labels==idx]=255
    return out

def local_element_metrics(element_img,roi,element):
    sig=resize_like(signal_channel(element_img,element),roi.shape); rb=roi>0
    if not np.any(rb): return {"contrast_pct":0,"log2_ratio":0,"zscore":0,"coverage":0,"overlap":0,"roi_mean":0,"bg_mean":0}
    dil=cv2.dilate(roi,np.ones((17,17),np.uint8)); ring=(dil>0)&(~rb)
    if int(ring.sum())<30:
        dil=cv2.dilate(roi,np.ones((31,31),np.uint8)); ring=(dil>0)&(~rb)
    inside=sig[rb].astype(np.float32); bg=sig[ring].astype(np.float32); whole=sig.astype(np.float32)
    if inside.size==0 or bg.size==0: return {"contrast_pct":0,"log2_ratio":0,"zscore":0,"coverage":0,"overlap":0,"roi_mean":float(np.mean(inside)) if inside.size else 0,"bg_mean":float(np.mean(bg)) if bg.size else 0}
    roi_mean=float(np.median(inside)); bg_mean=float(np.median(bg)); bg_std=float(np.std(bg)); eps=1.0
    ratio=(roi_mean+eps)/(bg_mean+eps); contrast=(roi_mean-bg_mean)/(bg_mean+eps)*100; log2=float(np.log2(ratio)); z=(roi_mean-bg_mean)/max(bg_std,1.0)
    p85=float(np.percentile(whole,85)); high=sig>=p85
    return {"contrast_pct":round(float(np.clip(contrast,-1000,1000)),2),"log2_ratio":round(log2,4),"zscore":round(float(np.clip(z,-20,20)),3),"coverage":round(float(np.mean(inside>=p85))*100,2),"overlap":round(float(np.sum(rb&high)/max(np.sum(rb),1))*100,2),"roi_mean":round(roi_mean,3),"bg_mean":round(bg_mean,3)}

def sigmoid(x): return 1/(1+np.exp(-np.clip(x,-20,20)))

def make_overlay(base,mask,title=None):
    out=base.copy(); contours,_=cv2.findContours(mask.copy(),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE); cv2.drawContours(out,contours,-1,(255,255,255),3); cv2.drawContours(out,contours,-1,(0,0,255),1)
    if title:
        cv2.rectangle(out,(8,8),(210,38),(20,20,20),-1); cv2.putText(out,title,(16,29),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1,cv2.LINE_AA)
    return out

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
    roi=detect_residue_mask(se_ref); roi_area=int(cv2.countNonZero(roi)); coverage=roi_area/max(roi.size,1)
    gray=cv2.cvtColor(se_ref,cv2.COLOR_BGR2GRAY); bg=cv2.GaussianBlur(gray,(0,0),7); contrast=cv2.absdiff(gray,bg); morph_contrast=float(np.mean(contrast[roi>0]))/255 if roi_area else 0
    morphology_score=float(np.clip(.55*np.clip(coverage/.06,0,1)+.45*min(morph_contrast*3,1),0,1))
    cm=local_element_metrics(c_map,roi,"C"); om=local_element_metrics(o_map,roi,"O"); nm=local_element_metrics(n_map,roi,"N"); sm=local_element_metrics(si_map,roi,"Si")
    ce=float(sigmoid((cm["zscore"]-.5)/1.5)); oe=float(sigmoid((om["zscore"]-.5)/1.5)); spatial=.5*np.clip(cm["overlap"]/100,0,1)+.5*np.clip(om["overlap"]/100,0,1)
    score=float(np.clip(.45*morphology_score+.20*ce+.20*oe+.15*spatial,0,1)); result="Residue" if score>=.50 else "Non-residue"; margin=abs(score-.50); agreement=(ce+oe)/2
    conf="High" if margin>=.22 and agreement>=.62 else "Medium" if margin>=.12 else "Low"
    reasons=[]
    if margin<.12: reasons.append("low_score_margin")
    if morphology_score>.60 and agreement<.40: reasons.append("SEM_strong_but_C_O_weak")
    if morphology_score<.25 and agreement>.70: reasons.append("C_O_signal_without_strong_SEM_candidate")
    return {"result":result,"confidence":conf,"residue_score":round(score,4),"c_enrichment":cm["contrast_pct"],"o_enrichment":om["contrast_pct"],"c_log2_ratio":cm["log2_ratio"],"o_log2_ratio":om["log2_ratio"],"c_zscore":cm["zscore"],"o_zscore":om["zscore"],"c_coverage":cm["coverage"],"o_coverage":om["coverage"],"c_spatial_overlap":cm["overlap"],"o_spatial_overlap":om["overlap"],"spatial_overlap":round(spatial*100,2),"n_enrichment":nm["contrast_pct"],"n_log2_ratio":nm["log2_ratio"],"n_zscore":nm["zscore"],"n_coverage":nm["coverage"],"si_enrichment":sm["contrast_pct"],"si_zscore":sm["zscore"],"morphology_score":round(morphology_score,4),"cluster_score":round(spatial,4),"roi_area_px":roi_area,"candidate_coverage":round(coverage*100,3),"review_reasons":reasons,"n_note":"N is extracted as a relative map signal and is not used in the current residue score.","si_note":"Si is stored as a relative map signal and is not used in the current residue score.","classification_note":"Binary CV classification uses Page-2 SE morphology plus local C/O signal and spatial overlap. Percent contrast is a relative image metric, not wt%/at%."},roi

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
    raw={"sem":p1["sem"],"eds_map":p1["eds_map"],"se_map":p2["se_map"],"c_map":p2["c_map"],"n_map":p2["n_map"],"o_map":p2["o_map"],"si_map":p2["si_map"]}
    for key,im in raw.items(): fp=outdir/f"{key}.jpg"; save_crop(im,fp); paths[key]=str(fp)
    # viewer-only enhanced/overlay assets
    roi_sem=cv2.resize(roi,(p1["sem"].shape[1],p1["sem"].shape[0]),interpolation=cv2.INTER_NEAREST); fp=outdir/"sem_residue_overlay.jpg"; save_crop(make_overlay(p1["sem"],roi_sem,"Residue candidate"),fp,92); paths["sem_residue_overlay"]=str(fp)
    fp=outdir/"element_maps_enhanced.jpg"; save_crop(make_element_overview(p2,roi),fp,92); paths["element_maps_enhanced"]=str(fp)
    for label,key in [("C","c_map"),("N","n_map"),("O","o_map"),("Si","si_map")]:
        enh=enhance_element_map(p2[key],label); rr=cv2.resize(roi,(enh.shape[1],enh.shape[0]),interpolation=cv2.INTER_NEAREST); fp=outdir/f"{key}_enhanced_overlay.jpg"; save_crop(make_overlay(enh,rr,f"{label} / residue ROI"),fp,92); paths[f"{key}_enhanced_overlay"]=str(fp)
    features.update({"map_parser":"Bruker page-2 SE-referenced individual C/N/O/Si panels","page":payload["page"],"cv_version":"v12-local-ring-spatial","viewer_note":"Enhanced/overlay images are visualization assets only; raw maps remain stored separately."})
    del roi; p1.clear(); p2.clear(); raw.clear(); gc.collect()
    rec={"id":payload["id"],"power":payload["power"],"time":payload["time"],"wafer":payload["wafer"],"point":payload["point"],"zone":payload["zone"],"condition":payload["condition"],"page":payload["page"],"pages_per_point":payload["pages_per_point"],"source_pages":payload["source_pages"],"assets":paths,"features":features}
    Path(payload["result_path"]).write_text(json.dumps(rec,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: python point_worker.py <point_input.json>")
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
