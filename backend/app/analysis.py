import base64, json, math, re
from pathlib import Path
import cv2
import fitz
import numpy as np

ZONE_MAP = {1:"Center",2:"Center",3:"Center",4:"Edge",5:"Diamond",6:"Edge",7:"Edge",8:"Center",9:"Diamond"}

def parse_page_identity(text: str, page_number: int):
    text = text.replace("\n", " ")
    m = re.search(r"(\d{2,4})\s*W[_\s-]*(\d{1,4})\s*S", text, re.I)
    power = f"{m.group(1)}W" if m else None
    time = f"{m.group(2)}s" if m else None
    wm = re.search(r"\bW(?:afer)?\s*([1-9])\b", text, re.I)
    pm = re.search(r"\bP(?:oint)?\s*([1-9])\b", text, re.I)
    wafer = int(wm.group(1)) if wm else None
    point = int(pm.group(1)) if pm else None
    # Fallback for filenames/page text such as 150W_120S_W1_P1.
    if not power:
        fm = re.search(r"(\d{2,4})W[_\s-]*(\d{1,4})S[_\s-]*W([1-9])[_\s-]*P([1-9])", text, re.I)
        if fm:
            power, time, wafer, point = f"{fm.group(1)}W", f"{fm.group(2)}s", int(fm.group(3)), int(fm.group(4))
    return power, time, wafer, point

def render_page(page, scale=2.0):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

def crop_layout(img):
    h,w = img.shape[:2]
    # Layout based on the user's standard report: SEM / Spectrum on top,
    # EDS Map / Element Maps in the middle, data/result at bottom.
    return {
        "sem": img[int(.10*h):int(.40*h), int(.02*w):int(.49*w)],
        "spectrum": img[int(.10*h):int(.40*h), int(.51*w):int(.98*w)],
        "eds_map": img[int(.42*h):int(.70*h), int(.02*w):int(.49*w)],
        "element_maps": img[int(.42*h):int(.70*h), int(.51*w):int(.98*w)],
        "data": img[int(.72*h):int(.97*h), int(.02*w):int(.60*w)],
        "result": img[int(.72*h):int(.97*h), int(.62*w):int(.98*w)],
    }

def encode_jpeg(img):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return base64.b64encode(buf.tobytes()).decode() if ok else ""

def save_crop(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)

def residue_features(sem, c_map=None, o_map=None):
    gray=cv2.cvtColor(sem, cv2.COLOR_BGR2GRAY) if len(sem.shape)==3 else sem
    bg=cv2.GaussianBlur(gray,(0,0),9)
    top=cv2.normalize(cv2.absdiff(gray,bg),None,0,255,cv2.NORM_MINMAX)
    _,mask=cv2.threshold(top,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    kernel=np.ones((5,5),np.uint8)
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,kernel)
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,kernel)
    n,labels,stats,_=cv2.connectedComponentsWithStats(mask)
    roi=np.zeros_like(mask)
    if n>1:
        idx=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
        roi[labels==idx]=255
    else:
        roi=mask
    roi_area=max(int((roi>0).sum()),1)
    coverage=roi_area/max(mask.size,1)

    def enrich(im):
        if im is None: return 0.0,0.0
        g=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY) if len(im.shape)==3 else im
        inside=g[roi>0].astype(float)
        outside=g[roi==0].astype(float)
        if len(inside)==0 or len(outside)==0:return 0.0,0.0
        base=max(float(outside.mean()),1.0)
        return float(np.clip((inside.mean()-base)/base, -1, 3)), float((inside>np.percentile(g,70)).mean())
    ce,cc=enrich(c_map)
    oe,oc=enrich(o_map)
    # If maps are unavailable, use morphology-derived SEM evidence.
    morphology_score=float(np.clip(coverage*8 + np.std(top)/255.0,0,1))
    cscore=float(np.clip((ce+1)/2,0,1))
    oscore=float(np.clip((oe+1)/2,0,1))
    cluster=float(np.clip(.45*morphology_score+.275*cscore+.275*oscore,0,1))
    residue_score=float(np.clip(.45*cscore+.35*oscore+.20*morphology_score,0,1))
    if residue_score>=.62: result="Residue"
    elif residue_score<=.38: result="Non-residue"
    else: result="Review"
    confidence="High" if abs(residue_score-.5)>=.25 else "Medium" if abs(residue_score-.5)>=.12 else "Low"
    return {
        "result":result,
        "confidence":confidence,
        "residue_score":round(residue_score,4),
        "c_enrichment":round(ce*100,2),
        "o_enrichment":round(oe*100,2),
        "c_coverage":round(cc*100,2),
        "o_coverage":round(oc*100,2),
        "cluster_score":round(cluster,4),
        "roi_area_px":roi_area,
        "candidate_coverage":round(coverage*100,3)
    }

def extract_pdf(pdf_path: Path, output_dir: Path):
    doc=fitz.open(pdf_path)
    records=[]
    for i,page in enumerate(doc):
        text=page.get_text("text")
        power,time,wafer,point=parse_page_identity(text,i+1)
        if not (power and time and wafer and point):
            continue
        img=render_page(page)
        crops=crop_layout(img)
        point_id=f"{power}_{time}_W{wafer}_P{point}"
        pdir=output_dir/point_id
        pdir.mkdir(parents=True,exist_ok=True)
        paths={}
        for key,crop in crops.items():
            fp=pdir/f"{key}.jpg"
            save_crop(crop,fp)
            paths[key]=str(fp)
        # The page is the canonical source. C/O maps can later be selected
        # from vendor-specific exports; for now we use EDS map / element maps
        # as optional inputs to the CV engine.
        feat=residue_features(crops["sem"], crops["element_maps"], crops["element_maps"])
        rec={
            "id":point_id,"power":power,"time":time,"wafer":wafer,"point":point,
            "zone":ZONE_MAP.get(point,"Unknown"),"page":i+1,
            "assets":paths,"features":feat,
            "source_text":text[:4000]
        }
        records.append(rec)
    return records

def image_to_data_url(path):
    data=Path(path).read_bytes()
    ext=Path(path).suffix.lower().replace(".","") or "jpeg"
    return f"data:image/{'jpeg' if ext=='jpg' else ext};base64,{base64.b64encode(data).decode()}"
