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
try:
    from .classification import C_RESIDUE_RATIO, O_RESIDUE_RATIO, MODERATE_RATIO, co_rule_result
except ImportError:  # point_worker.py is launched as a standalone subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.classification import C_RESIDUE_RATIO, O_RESIDUE_RATIO, MODERATE_RATIO, co_rule_result
cv2.setNumThreads(1)
try: cv2.ocl.setUseOpenCL(False)
except Exception: pass


def render_page_to_jpeg(page, quality=92):
    pix=page.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
    try: data=pix.tobytes("jpeg",jpg_quality=quality)
    finally: del pix
    return data


LEGACY_PAGE1_SEM_BOX={"x0":98,"y0":207,"x1":726,"y1":625}
LEGACY_PAGE1_EDS_BOX={"x0":101,"y0":818,"x1":1203,"y1":1552}
LEGACY_PAGE2_BOXES={
    "se_map":{"x0":101,"y0":190,"x1":643,"y1":552},
    "c_map":{"x0":658,"y0":190,"x1":1200,"y1":552},
    "n_map":{"x0":101,"y0":566,"x1":643,"y1":928},
    "o_map":{"x0":658,"y0":566,"x1":1200,"y1":928},
    "si_map":{"x0":101,"y0":943,"x1":643,"y1":1305},
}


def _rect_dict(x,y,w,h):
    return {"x0":int(x),"y0":int(y),"x1":int(x+w),"y1":int(y+h),"width":int(w),"height":int(h)}


def _crop_rect(img,rect):
    h,w=img.shape[:2]
    x0=max(0,min(int(rect["x0"]),w-1)); x1=max(x0+1,min(int(rect["x1"]),w))
    y0=max(0,min(int(rect["y0"]),h-1)); y1=max(y0+1,min(int(rect["y1"]),h))
    return img[y0:y1,x0:x1]


def _scaled_rect(rect,shape):
    h,w=shape[:2]; sx=w/1240.0; sy=h/1754.0
    x0=round(rect["x0"]*sx); x1=round(rect["x1"]*sx)
    y0=round(rect["y0"]*sy); y1=round(rect["y1"]*sy)
    return _rect_dict(x0,y0,x1-x0,y1-y0)


def _large_nonwhite_components(img):
    """Return large continuous Bruker image panels on the white PDF page.

    The old and newer Bruker exports use the same page canvas but different
    panel heights.  Their image panels are continuous non-white rectangles, so
    detecting those rectangles is considerably more reliable than hard-coding
    one set of y coordinates.
    """
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY) if img.ndim==3 else img
    mask=(gray<250).astype(np.uint8)*255
    n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    out=[]
    for i in range(1,n):
        x,y,ww,hh,area=[int(v) for v in stats[i]]
        if ww<80 or hh<60: continue
        out.append({"x":x,"y":y,"w":ww,"h":hh,"area":area})
    return out


def _detect_page1_boxes(img):
    h,w=img.shape[:2]
    comps=_large_nonwhite_components(img)
    candidates=[c for c in comps if c["w"]>=w*0.40 and c["h"]>=h*0.17 and c["area"]>=w*h*0.08]
    if len(candidates)<2: return None
    candidates=sorted(candidates,key=lambda c:c["y"])
    sem_candidates=[c for c in candidates if c["y"]<h*0.48 and c["w"]<w*0.72]
    eds_candidates=[c for c in candidates if c["y"]>h*0.40 and c["w"]>w*0.72]
    if not sem_candidates or not eds_candidates: return None
    sem=max(sem_candidates,key=lambda c:c["area"]); eds=max(eds_candidates,key=lambda c:c["area"] )
    return _rect_dict(sem["x"],sem["y"],sem["w"],sem["h"]),_rect_dict(eds["x"],eds["y"],eds["w"],eds["h"])


def _detect_page2_boxes(img):
    h,w=img.shape[:2]
    comps=_large_nonwhite_components(img)
    panels=[c for c in comps if w*0.35<=c["w"]<=w*0.56 and h*0.16<=c["h"]<=h*0.29 and c["area"]>=w*h*0.075]
    if len(panels)<5: return None
    # There should be exactly five large map panels. If text/logo components
    # happen to pass the coarse filter, keep the five largest image rectangles.
    panels=sorted(panels,key=lambda c:c["area"],reverse=True)[:5]
    panels=sorted(panels,key=lambda c:(c["y"],c["x"]))
    # Cluster the five panels into 2 / 2 / 1 rows by y position.
    rows=[]
    for c in panels:
        placed=False
        for row in rows:
            if abs(c["y"]-np.median([r["y"] for r in row]))<=max(12,int(h*0.012)):
                row.append(c); placed=True; break
        if not placed: rows.append([c])
    rows=sorted(rows,key=lambda row:min(c["y"] for c in row))
    if len(rows)!=3 or sorted(len(r) for r in rows)!=[1,2,2]: return None
    if len(rows[0])!=2 or len(rows[1])!=2 or len(rows[2])!=1: return None
    r1=sorted(rows[0],key=lambda c:c["x"]); r2=sorted(rows[1],key=lambda c:c["x"]); r3=sorted(rows[2],key=lambda c:c["x"])

    # v23.9.8: Bruker panels in one Page-2 export share the same analytical
    # coordinate system.  Connected-component detection can differ by 1-2 px
    # between C/O/N/Si because a dark border or footer pixel is sometimes merged
    # into one panel but not another.  Using each raw component size separately
    # therefore produced e.g. C=(408,543), O=(409,544), which later crashed the
    # C+O overlay and also made ROI coordinates subtly inconsistent.
    #
    # Canonicalize the five detected panels onto one common width/height while
    # preserving the detected row/column anchors.  The median is robust to those
    # tiny border differences and reflects the true Bruker panel geometry.
    allp=r1+r2+r3
    panel_w=int(round(float(np.median([c["w"] for c in allp]))))
    panel_h=int(round(float(np.median([c["h"] for c in allp]))))
    left_x=int(round(float(np.median([r1[0]["x"],r2[0]["x"],r3[0]["x"]]))))
    right_x=int(round(float(np.median([r1[1]["x"],r2[1]["x"]]))))
    row1_y=int(round(float(np.median([c["y"] for c in r1]))))
    row2_y=int(round(float(np.median([c["y"] for c in r2]))))
    row3_y=int(round(float(r3[0]["y"])))

    # Clamp only if the page edge would be exceeded; normally all panels remain
    # exactly panel_w x panel_h.
    panel_w=max(1,min(panel_w,w-max(left_x,right_x)))
    panel_h=max(1,min(panel_h,h-max(row1_y,row2_y,row3_y)))
    boxes={
        "se_map":_rect_dict(left_x,row1_y,panel_w,panel_h),
        "c_map":_rect_dict(right_x,row1_y,panel_w,panel_h),
        "n_map":_rect_dict(left_x,row2_y,panel_w,panel_h),
        "o_map":_rect_dict(right_x,row2_y,panel_w,panel_h),
        "si_map":_rect_dict(left_x,row3_y,panel_w,panel_h),
    }
    return boxes


def _layout_profile(page2_boxes):
    heights=[int(v["y1"]-v["y0"]) for v in page2_boxes.values()]
    med=int(round(float(np.median(heights)))) if heights else 0
    if 350<=med<=375: return "bruker_standard_362"
    if 395<=med<=420: return "bruker_tall_407"
    return f"bruker_auto_{med}"


def crop_page1_layout(img):
    """Auto-detect Page-1 SEM and Full EDS panels across Bruker export layouts.

    When the classic 734-pixel Full-EDS panel is detected, keep the historically
    verified v13 SEM crop *exactly* unchanged so already-correct 1st-layout data
    does not move by even one border pixel.  Taller exports use their detected
    rectangles.
    """
    detected=_detect_page1_boxes(img)
    if detected:
        detected_sem,detected_eds=detected
        if abs(int(detected_eds["height"])-round(734*img.shape[0]/1754.0))<=3:
            sem_box=_scaled_rect(LEGACY_PAGE1_SEM_BOX,img.shape); eds_box=_scaled_rect(LEGACY_PAGE1_EDS_BOX,img.shape); method="legacy_standard_profile"
        else:
            sem_box,eds_box=detected_sem,detected_eds; method="auto_nonwhite_components"
    else:
        sem_box=_scaled_rect(LEGACY_PAGE1_SEM_BOX,img.shape); eds_box=_scaled_rect(LEGACY_PAGE1_EDS_BOX,img.shape); method="legacy_fixed_fallback"
    return {
        "sem":_crop_rect(img,sem_box),"eds_map":_crop_rect(img,eds_box),
        "_layout":{"method":method,"sem_box":sem_box,"eds_box":eds_box,"page_width":int(img.shape[1]),"page_height":int(img.shape[0])}
    }


def crop_page2_element_maps(img):
    """Auto-detect the five Bruker Page-2 element panels.

    Supports both the original 542x362 panels and the later ~542x407 panels
    without condition-specific rules.
    """
    boxes=_detect_page2_boxes(img)
    method="auto_nonwhite_components"
    if not boxes:
        boxes={k:_scaled_rect(v,img.shape) for k,v in LEGACY_PAGE2_BOXES.items()}
        method="legacy_fixed_fallback"
    x0=min(v["x0"] for v in boxes.values()); y0=min(v["y0"] for v in boxes.values())
    x1=max(v["x1"] for v in boxes.values()); y1=max(v["y1"] for v in boxes.values())
    full={"x0":x0,"y0":y0,"x1":x1,"y1":y1,"width":x1-x0,"height":y1-y0}
    return {
        "full_element_maps_original":_crop_rect(img,full),
        "se_map":_crop_rect(img,boxes["se_map"]),"c_map":_crop_rect(img,boxes["c_map"]),
        "n_map":_crop_rect(img,boxes["n_map"]),"o_map":_crop_rect(img,boxes["o_map"]),
        "si_map":_crop_rect(img,boxes["si_map"]),
        "_layout":{"method":method,"profile":_layout_profile(boxes),"boxes":boxes,"full_box":full,"page_width":int(img.shape[1]),"page_height":int(img.shape[0])}
    }


def remap_normalized_polygons_between_crops(polygons,old_box,new_box):
    """Move normalized Human-ROI polygons from an old SEM crop into a new crop.

    Coordinates are converted through the common rendered PDF page coordinate
    system, so a Human ROI drawn against the legacy fixed crop survives a
    re-analysis that switches to the auto-detected SEM panel.
    """
    if not polygons: return []
    # Normalize one polygon vs list-of-polygons.
    if isinstance(polygons,list) and polygons and isinstance(polygons[0],dict):
        polygons=[polygons]
    try:
        ox0,oy0,ox1,oy1=[float(old_box[k]) for k in ("x0","y0","x1","y1")]
        nx0,ny0,nx1,ny1=[float(new_box[k]) for k in ("x0","y0","x1","y1")]
    except Exception:
        return polygons
    ow=max(ox1-ox0,1.0); oh=max(oy1-oy0,1.0); nw=max(nx1-nx0,1.0); nh=max(ny1-ny0,1.0)
    out=[]
    for poly in polygons:
        if not isinstance(poly,list): continue
        pts=[]
        for q in poly:
            if not isinstance(q,dict): continue
            try:
                x=float(q.get("x",0)); y=float(q.get("y",0))
            except Exception:
                continue
            page_x=ox0+np.clip(x,0,1)*ow; page_y=oy0+np.clip(y,0,1)*oh
            rx=float(np.clip((page_x-nx0)/nw,0,1)); ry=float(np.clip((page_y-ny0)/nh,0,1))
            pts.append({"x":round(rx,6),"y":round(ry,6)})
        if len(pts)>=3: out.append(pts)
    return out


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


def _registration_signal(im, shape):
    """High-pass grayscale image used to register Page-1 SEM to Page-2 SE.

    The SEM and EDS element panels are separate captures and can be translated by a
    few pixels.  Resizing an SEM ROI directly onto C/O therefore causes the white
    contour to drift.  Registration uses the Page-2 SE panel as the geometry bridge;
    C/N/O/Si panels share that Page-2 coordinate system.
    """
    h,w=shape[:2]
    src=im
    if src.shape[:2]!=(h,w):
        src=cv2.resize(src,(w,h),interpolation=cv2.INTER_AREA)
    gray=cv2.cvtColor(src,cv2.COLOR_BGR2GRAY) if src.ndim==3 else src.copy()
    gray=cv2.createCLAHE(clipLimit=1.6,tileGridSize=(8,8)).apply(gray)
    g=gray.astype(np.float32)
    # High-pass removes different overall brightness/color-map rendering while
    # retaining particle edges and local morphology shared by SEM and SE.
    hp=g-cv2.GaussianBlur(g,(0,0),5.0)
    usable=analytical_mask((h,w),footer_fraction=0.82)>0
    hp[~usable]=0.0
    hp-=float(hp[usable].mean()) if np.any(usable) else 0.0
    return hp

def _axis_activity_bounds(gray, axis):
    """Return the longest textured image-content run on rows/columns.

    Bruker element panels can contain different top/bottom bands even when the
    nominal panel crop has the same size.  We detect the actual textured image
    rectangle from geometry/texture only; elemental intensity is never optimized,
    so C/O evidence cannot pull the ROI toward a brighter signal.
    """
    g=gray.astype(np.float32)
    if axis==0:  # rows
        lo=np.percentile(g,10,axis=1); hi=np.percentile(g,90,axis=1)
        texture=np.mean(np.abs(np.diff(g,axis=1)),axis=1) if g.shape[1]>1 else np.zeros(g.shape[0])
    else:  # columns
        lo=np.percentile(g,10,axis=0); hi=np.percentile(g,90,axis=0)
        texture=np.mean(np.abs(np.diff(g,axis=0)),axis=0) if g.shape[0]>1 else np.zeros(g.shape[1])
    activity=(hi-lo)+0.85*texture
    n=len(activity)
    if n<8 or not np.any(np.isfinite(activity)):
        return 0,n,0.0
    a=np.nan_to_num(activity,nan=0.0,posinf=0.0,neginf=0.0)
    p35=float(np.percentile(a,35)); p80=float(np.percentile(a,80))
    threshold=max(1.8,p35+0.18*max(p80-p35,0.0))
    active=(a>threshold).astype(np.uint8)*255
    # Bridge short gaps inside the analytical field, but keep broad uniform
    # header/footer bands separate from the real map image.
    k=max(3,int(round(n*0.018)))
    if k%2==0: k+=1
    active=cv2.morphologyEx(active.reshape(-1,1),cv2.MORPH_CLOSE,np.ones((k,1),np.uint8)).ravel()
    runs=[]; start=None
    for i,v in enumerate(active):
        if v and start is None: start=i
        if start is not None and (not v or i==n-1):
            end=i if not v else i+1
            if end-start>=max(6,int(n*0.08)):
                score=(end-start)*(float(np.median(a[start:end]))+1.0)
                runs.append((score,start,end))
            start=None
    if not runs:
        return 0,n,0.0
    _,start,end=max(runs,key=lambda z:z[0])
    if end-start<int(n*0.24):
        return 0,n,0.0
    pad=max(1,int(n*0.004))
    start=max(0,start-pad); end=min(n,end+pad)
    confidence=float(np.clip((end-start)/max(n,1),0,1))
    return int(start),int(end),round(confidence,4)


def estimate_panel_content_rect(im):
    """Estimate the true analytical image rectangle inside an element-map panel.

    First find the non-black panel footprint, then trim only broad uniform
    header/footer bands.  This is intentionally geometry-only; it never moves
    the ROI toward a stronger C/O signal.
    """
    if im is None or getattr(im,'size',0)==0:
        return {"x0":0,"y0":0,"x1":0,"y1":0,"confidence":0.0,"method":"full_panel_fallback"}
    arr=im if im.ndim==3 else cv2.cvtColor(im,cv2.COLOR_GRAY2BGR)
    gray=cv2.cvtColor(arr,cv2.COLOR_BGR2GRAY)
    h,w=gray.shape[:2]
    peak=np.max(arr,axis=2)
    nonblack=peak>6
    row_occ=np.mean(nonblack,axis=1); col_occ=np.mean(nonblack,axis=0)
    rows=np.where(row_occ>0.08)[0]; cols=np.where(col_occ>0.08)[0]
    if len(rows)<int(h*0.30) or len(cols)<int(w*0.30):
        return {"x0":0,"y0":0,"x1":w,"y1":h,"confidence":0.0,"method":"full_panel_fallback"}
    y0,y1=int(rows[0]),int(rows[-1]+1); x0,x1=int(cols[0]),int(cols[-1]+1)

    def row_activity(y):
        line=gray[y,x0:x1].astype(np.float32)
        if line.size<4: return 0.0
        return float(np.percentile(line,90)-np.percentile(line,10)+0.85*np.mean(np.abs(np.diff(line))))

    acts=np.array([row_activity(y) for y in range(y0,y1)],np.float32)
    positive=acts[acts>0.5]
    if positive.size:
        threshold=max(1.5,float(np.median(positive))*0.18)
        good=acts>threshold
        # Require 3 nearby textured rows so one label/scale-bar line cannot define
        # the beginning of the analytical image.
        kernel=np.ones(3,np.uint8)
        stable=cv2.morphologyEx((good.astype(np.uint8)*255).reshape(-1,1),cv2.MORPH_CLOSE,kernel.reshape(-1,1)).ravel()>0
        idx=np.where(stable)[0]
        if len(idx):
            ny0=y0+int(idx[0]); ny1=y0+int(idx[-1]+1)
            # Only trim broad bands; never collapse onto the residue itself.
            if ny1-ny0>=int((y1-y0)*0.55):
                y0,y1=ny0,ny1

    # Columns are usually already aligned by the panel crop. Trim only obvious
    # black side borders, not low-signal element-map regions.
    width=x1-x0; height=y1-y0
    if width<int(w*0.35) or height<int(h*0.35):
        return {"x0":0,"y0":0,"x1":w,"y1":h,"confidence":0.0,"method":"full_panel_fallback"}
    confidence=float(np.clip(min(width/max(w,1),height/max(h,1)),0,1))
    return {"x0":x0,"y0":y0,"x1":x1,"y1":y1,"confidence":round(confidence,4),"method":"nonblack_rect_uniform_band_trim"}


def _paste_rect_mask(src_mask, src_rect, target_shape, target_rect):
    th,tw=target_shape[:2]
    out=np.zeros((th,tw),np.uint8)
    sx0,sy0,sx1,sy1=[int(src_rect[k]) for k in ("x0","y0","x1","y1")]
    tx0,ty0,tx1,ty1=[int(target_rect[k]) for k in ("x0","y0","x1","y1")]
    sx0=max(0,min(sx0,src_mask.shape[1]-1)); sx1=max(sx0+1,min(sx1,src_mask.shape[1]))
    sy0=max(0,min(sy0,src_mask.shape[0]-1)); sy1=max(sy0+1,min(sy1,src_mask.shape[0]))
    tx0=max(0,min(tx0,tw-1)); tx1=max(tx0+1,min(tx1,tw))
    ty0=max(0,min(ty0,th-1)); ty1=max(ty0+1,min(ty1,th))
    crop=src_mask[sy0:sy1,sx0:sx1]
    if crop.size==0: return out
    mapped=cv2.resize(crop,(tx1-tx0,ty1-ty0),interpolation=cv2.INTER_NEAREST)
    out[ty0:ty1,tx0:tx1]=mapped
    return out


def map_sem_roi_to_element(roi,target_image,se_map=None,registration=None,return_info=False):
    """Map SEM ROI to an element panel using one shared Page-2 geometry.

    C/N/O/Si panels in the Bruker Page-2 grid represent the same field of view
    and pixel coordinate system.  They must therefore receive the *same ROI mask*.
    v23.9.3 tried to infer a separate content rectangle for each element panel;
    that could make O drift vertically relative to C.  v23.9.4 intentionally
    removes all element-specific shifts.
    """
    if target_image is None or getattr(target_image,'size',0)==0:
        shape=(0,0) if target_image is None else target_image.shape
        out=np.zeros(shape[:2],np.uint8) if len(shape)>=2 else np.zeros((0,0),np.uint8)
        return (out,{"method":"missing_target"}) if return_info else out
    bridge_shape=(se_map.shape if se_map is not None and getattr(se_map,'size',0) else target_image.shape)
    shared=map_sem_roi_to_eds(roi,bridge_shape,registration)
    if shared.shape[:2]!=target_image.shape[:2]:
        shared=cv2.resize(shared,(target_image.shape[1],target_image.shape[0]),interpolation=cv2.INTER_NEAREST)
    info={
        "method":"shared_page2_geometry",
        "target_width":int(target_image.shape[1]),"target_height":int(target_image.shape[0]),
        "registration_method":(registration or {}).get("method","resize_fallback"),
        "scale":(registration or {}).get("scale"),"rotation_deg":(registration or {}).get("rotation_deg"),
    }
    return (shared,info) if return_info else shared

def _similarity_from_sift(sem_ref, se_map):
    """Estimate SEM -> Page-2 SE similarity transform with SIFT + RANSAC.

    Page-1 SEM and Page-2 SE can use different fields of view/magnifications, so
    translation-only registration is not sufficient.  This transform allows one
    shared isotropic scale, a small rotation, and translation.  The result maps
    coordinates in the *original SEM crop* directly into Page-2 SE pixels.
    """
    try:
        if sem_ref is None or se_map is None or getattr(sem_ref,'size',0)==0 or getattr(se_map,'size',0)==0:
            return None
        sgray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY) if sem_ref.ndim==3 else sem_ref.copy()
        tgray=cv2.cvtColor(se_map,cv2.COLOR_BGR2GRAY) if se_map.ndim==3 else se_map.copy()
        # Remove report footer/scale-bar rows from feature detection while keeping
        # the original coordinate system intact.
        smask=analytical_mask(sgray.shape,footer_fraction=0.82)
        tmask=analytical_mask(tgray.shape,footer_fraction=0.82)
        clahe=cv2.createCLAHE(clipLimit=1.8,tileGridSize=(8,8))
        sgray=clahe.apply(sgray); tgray=clahe.apply(tgray)
        sift=cv2.SIFT_create(nfeatures=1400,contrastThreshold=0.018,edgeThreshold=12,sigma=1.4)
        ks,ds=sift.detectAndCompute(sgray,smask)
        kt,dt=sift.detectAndCompute(tgray,tmask)
        if ds is None or dt is None or len(ks)<6 or len(kt)<6:
            return None
        matcher=cv2.BFMatcher(cv2.NORM_L2)
        pairs=matcher.knnMatch(ds,dt,k=2)
        good=[]
        for pair in pairs:
            if len(pair)<2: continue
            m,n=pair
            if m.distance < 0.74*n.distance:
                good.append(m)
        if len(good)<5:
            return None
        src=np.float32([ks[m.queryIdx].pt for m in good]).reshape(-1,1,2)
        dst=np.float32([kt[m.trainIdx].pt for m in good]).reshape(-1,1,2)
        M,inliers=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=4.5,maxIters=4000,confidence=0.995,refineIters=25)
        if M is None:
            return None
        inlier_count=int(inliers.sum()) if inliers is not None else 0
        a,b=float(M[0,0]),float(M[1,0])
        scale=float((a*a+b*b)**0.5)
        rotation=float(np.degrees(np.arctan2(b,a)))
        sh,sw=sgray.shape[:2]; th,tw=tgray.shape[:2]
        inlier_ratio=inlier_count/max(len(good),1)
        # Typical reports differ by magnification but not by large rotation or
        # anisotropic distortion.  Keep bounds broad enough for 10/20 um FOV changes.
        plausible=(np.isfinite(scale) and np.isfinite(rotation)
                   and 0.22<=scale<=1.35 and abs(rotation)<=10.0
                   and inlier_count>=4 and inlier_ratio>=0.28)
        if not plausible:
            return None
        return {
            "accepted":True,
            "method":"sift_similarity",
            "matrix":[[round(float(v),8) for v in row] for row in M.tolist()],
            "scale":round(scale,5),"rotation_deg":round(rotation,4),
            "matches":len(good),"inliers":inlier_count,"inlier_ratio":round(inlier_ratio,4),
            "source_width":sw,"source_height":sh,"width":tw,"height":th,
        }
    except Exception:
        return None


def estimate_sem_to_eds_registration(sem_ref,se_map):
    """Estimate SEM -> Page-2 SE transform.

    v23.9.5 retains the v23.9.4 similarity transform (scale + small rotation +
    translation).  This is required because Page-1 SEM and Page-2 EDS/SE can use
    different magnifications/FOVs.  If feature registration is not reliable, the
    older conservative resize + translation fallback is retained.
    """
    if sem_ref is None or se_map is None or getattr(sem_ref,'size',0)==0 or getattr(se_map,'size',0)==0:
        return {"accepted":False,"dx_px":0.0,"dy_px":0.0,"response":0.0,"method":"resize_fallback"}
    sim=_similarity_from_sift(sem_ref,se_map)
    if sim:
        return sim
    h,w=se_map.shape[:2]
    try:
        a=_registration_signal(sem_ref,(h,w)); b=_registration_signal(se_map,(h,w))
        window=cv2.createHanningWindow((w,h),cv2.CV_32F)
        (dx,dy),response=cv2.phaseCorrelate(a,b,window)
        dx=float(dx); dy=float(dy); response=float(response)
        plausible=(np.isfinite(dx) and np.isfinite(dy) and np.isfinite(response)
                   and abs(dx)<=w*0.12 and abs(dy)<=h*0.12 and response>=0.015)
        if not plausible:
            return {"accepted":False,"dx_px":0.0,"dy_px":0.0,"response":round(response if np.isfinite(response) else 0.0,4),"method":"resize_fallback","width":w,"height":h,"source_width":sem_ref.shape[1],"source_height":sem_ref.shape[0]}
        return {"accepted":True,"dx_px":round(dx,3),"dy_px":round(dy,3),"response":round(response,4),"method":"resize_phase_translation","width":w,"height":h,"source_width":sem_ref.shape[1],"source_height":sem_ref.shape[0]}
    except Exception:
        return {"accepted":False,"dx_px":0.0,"dy_px":0.0,"response":0.0,"method":"resize_fallback","width":w,"height":h,"source_width":sem_ref.shape[1],"source_height":sem_ref.shape[0]}


def map_sem_roi_to_eds(roi,target_shape,registration=None):
    """Map an SEM-space ROI into the shared Page-2 EDS coordinate system."""
    th,tw=target_shape[:2]
    if roi is None:
        return np.zeros((th,tw),np.uint8)
    reg=registration or {}
    # Similarity matrix is defined from original SEM pixels directly to the
    # Page-2 SE coordinate system; do not pre-resize the mask in this path.
    if reg.get("accepted") and reg.get("method")=="sift_similarity" and reg.get("matrix"):
        try:
            M=np.asarray(reg["matrix"],dtype=np.float32).reshape(2,3).copy()
            rw=int(reg.get("width") or tw); rh=int(reg.get("height") or th)
            base=cv2.warpAffine((roi>0).astype(np.uint8)*255,M,(rw,rh),flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
            if (rh,rw)!=(th,tw):
                base=cv2.resize(base,(tw,th),interpolation=cv2.INTER_NEAREST)
            return base
        except Exception:
            pass
    # Legacy fallback: normalize to target size, then apply the small translation.
    rw=int(reg.get("width") or tw); rh=int(reg.get("height") or th)
    base=cv2.resize((roi>0).astype(np.uint8)*255,(rw,rh),interpolation=cv2.INTER_NEAREST)
    if reg.get("accepted") and reg.get("method")=="resize_phase_translation":
        M=np.float32([[1,0,float(reg.get("dx_px",0.0))],[0,1,float(reg.get("dy_px",0.0))]])
        base=cv2.warpAffine(base,M,(rw,rh),flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    if (rh,rw)!=(th,tw):
        base=cv2.resize(base,(tw,th),interpolation=cv2.INTER_NEAREST)
    return base

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
    raw_bg=cv2.GaussianBlur(raw,(0,0),31)
    delta=raw-raw_bg
    dmed=float(np.median(delta)); dmad=float(np.median(np.abs(delta-dmed))); drs=max(1.0,1.4826*dmad)
    for polarity in (1,-1):
        signed=delta*polarity
        threshold=max(9.0,2.80*drs)
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
        cand.append({"label":i,"score":float(morph_rank),"area":int(area),"bbox":(int(x),int(y),int(ww),int(hh)),"mask":cm,"polarity":1})
    # Add direct signed-background components separately. These are especially important
    # for large bright/dark residues whose interior is not a top-hat edge.
    for dc_idx,dc in enumerate(direct_cues):
        direct_polarity=1 if dc_idx==0 else -1
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
            cand.append({"label":20000+i,"score":float(morph_rank),"area":int(area),"bbox":(int(x),int(y),int(ww),int(hh)),"mask":cm,"direct_background":True,"polarity":direct_polarity})
    cand.sort(key=lambda z:z["score"],reverse=True)
    # Merge fragments that are close enough to plausibly belong to the same physical
    # residue. This fixes large/irregular residues being split into tiny candidates.
    merged=[]
    for c in cand:
        placed=False
        x,y,ww,hh=c["bbox"]
        for d in merged:
            if d.get("polarity") in (1,-1) and c.get("polarity") in (1,-1) and d.get("polarity") != c.get("polarity"):
                continue
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



def element_ratio_score(ratio, strong_ratio):
    """0-1 score with 2x=0.60 and the element-specific residue gate=0.70."""
    x=max(0.0,float(ratio)); strong=float(strong_ratio)
    if x < MODERATE_RATIO:
        return float(np.clip((x/MODERATE_RATIO)*0.59,0,0.59))
    if x < strong:
        return float(0.60 + ((x-MODERATE_RATIO)/(strong-MODERATE_RATIO))*0.10)
    equivalent=x*(3.0/strong)
    k=0.23
    denom=1.0-np.exp(-k*(20.0-3.0))
    normalized=(1.0-np.exp(-k*(equivalent-3.0)))/denom if denom else 0.0
    return float(min(1.0,0.70+0.30*max(0.0,normalized)))

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

def exclude_element_annotations(im):
    """Display-only: neutralize exactly the area excluded by global_roi_metrics.

    Coordinates and analytical pixels are unchanged. No ROI, ratios or decisions
    are recomputed. Keep labels/scale readable in monochrome for traceability.
    """
    out=im.copy()
    valid=analytical_mask(out.shape)>0
    mono=np.max(out,axis=2)
    out[~valid]=np.repeat(mono[~valid,None],3,axis=1)
    return out


def enhance_element_map(im,element):
    ch=signal_channel(im,element); p2,p98=np.percentile(ch,[2,98]); norm=np.clip((ch.astype(np.float32)-p2)*255/max(p98-p2,1),0,255).astype(np.uint8); norm=cv2.createCLAHE(clipLimit=2.2,tileGridSize=(8,8)).apply(norm)
    if element=="C": return cv2.merge([np.zeros_like(norm),np.zeros_like(norm),norm])
    if element=="N": return cv2.merge([np.zeros_like(norm),norm,np.zeros_like(norm)])
    if element=="O": return cv2.merge([norm,np.zeros_like(norm),np.zeros_like(norm)])
    if element=="Si": return cv2.merge([norm,norm,np.zeros_like(norm)])
    return cv2.cvtColor(norm,cv2.COLOR_GRAY2BGR)


def make_co_overlay(c_map,o_map,roi=None):
    """Create the Verification Full EDS C+O overlay used in the V21.1 UI."""
    c=signal_channel(c_map,"C").astype(np.float32)
    o=signal_channel(o_map,"O").astype(np.float32)
    def norm(a):
        p2,p98=np.percentile(a,[2,98])
        return np.clip((a-p2)*255/max(p98-p2,1),0,255)
    cn=norm(c); on=norm(o)
    # Defensive guard for legacy/cached crops created before v23.9.8. New Page-2
    # crops are canonicalized to identical dimensions, but an old asset can still
    # be a pixel wider/taller.  Normalize O to C before channel composition instead
    # of allowing NumPy broadcasting to abort the entire Point worker.
    if on.shape != cn.shape:
        on=cv2.resize(on,(cn.shape[1],cn.shape[0]),interpolation=cv2.INTER_LINEAR)
    out=np.zeros((cn.shape[0],cn.shape[1],3),np.uint8)
    # BGR: C=red, O=green, with a small blue component for readable overlap.
    out[:,:,2]=cn.astype(np.uint8)
    out[:,:,1]=on.astype(np.uint8)
    out[:,:,0]=np.clip((cn+on)*0.18,0,255).astype(np.uint8)
    if roi is not None:
        rr=cv2.resize(roi,(out.shape[1],out.shape[0]),interpolation=cv2.INTER_NEAREST)
        contours,_=cv2.findContours((rr>0).astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        if contours: cv2.drawContours(out,contours,-1,(255,255,255),3)
    return out


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

def refine_candidate_mask(sem_ref, candidate_mask, polarity_hint=None):
    """Turn a coarse candidate into a smooth physical residue mask.

    v21.4 uses hysteresis-style local-background segmentation instead of a fixed
    rectangular expansion. A strong core seeds a weaker connected region so a large
    residue is recovered as one contour, while isolated background grain and broad
    shadows are not promoted into the ROI.
    """
    gray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY).astype(np.float32)
    am=analytical_mask(gray.shape)>0
    cand=(candidate_mask>0)&am
    if not np.any(cand): return np.zeros_like(candidate_mask)

    # Smooth background follows illumination/shading but not the physical object.
    bg=cv2.GaussianBlur(gray,(0,0),25)
    signed=gray-bg
    vals=signed[cand]
    if vals.size<20: return candidate_mask.copy()
    med=float(np.median(vals))
    polarity=float(polarity_hint) if polarity_hint in (1,-1) else (1.0 if med>=0 else -1.0)
    signal=signed*polarity
    sigvals=signal[cand]

    # Strong core: robust enough to reject ordinary background fluctuations.
    mad=float(np.median(np.abs(sigvals-np.median(sigvals))))*1.4826
    p55=float(np.percentile(sigvals,55)); p75=float(np.percentile(sigvals,75))
    strong=max(3.0, p55+0.45*mad, p75*0.84)
    weak=max(1.8, strong*0.52)

    # Seed from the candidate itself, but retain only its strongest physical part.
    core=((cand)&(signal>=strong)).astype(np.uint8)*255
    core=cv2.morphologyEx(core,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)))
    core=cv2.morphologyEx(core,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
    n,lab,stats,_=cv2.connectedComponentsWithStats(core)
    if n>1:
        # Keep all strong pieces close to the original candidate; do not arbitrarily
        # choose one corner of a large irregular residue.
        keep=np.zeros_like(core)
        ys0,xs0=np.where(cand)
        cx0=float(np.mean(xs0)); cy0=float(np.mean(ys0))
        for i in range(1,n):
            yy,xx=np.where(lab==i)
            if len(xx)==0: continue
            cx=float(np.mean(xx)); cy=float(np.mean(yy))
            if np.hypot(cx-cx0,cy-cy0)<=max(gray.shape)*0.20:
                keep[lab==i]=255
        core=keep if cv2.countNonZero(keep)>0 else core
    if cv2.countNonZero(core)<12:
        core=cand.astype(np.uint8)*255

    # Weak mask is allowed to grow beyond the coarse detector. Hysteresis keeps only
    # weak regions connected to the strong core, which recovers long/tapered residue
    # bodies without drawing a rectangular ROI around the shadow.
    weak_mask=((signal>=weak)&am).astype(np.uint8)*255
    weak_mask=cv2.morphologyEx(weak_mask,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)))
    n,lab,stats,_=cv2.connectedComponentsWithStats(weak_mask)
    grown=np.zeros_like(weak_mask)
    core_dil=cv2.dilate(core,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)),iterations=1)
    for i in range(1,n):
        comp=(lab==i).astype(np.uint8)*255
        if cv2.countNonZero(cv2.bitwise_and(comp,core_dil)):
            grown[lab==i]=255

    # Limit expansion to a padded candidate neighborhood. This is deliberately
    # generous for large residues but prevents a global illumination gradient from
    # becoming one connected ROI.
    ys,xs=np.where(cand)
    if len(xs):
        x0,x1=int(xs.min()),int(xs.max()); y0,y1=int(ys.min()),int(ys.max())
        # Keep the expansion tight: the old 18% padding frequently captured the
        # long SEM charging/shadow halo around a real residue. The physical body
        # should define the ROI; expansion is only for reconnecting a broken edge.
        px=max(7,int((x1-x0+1)*0.10)); py=max(7,int((y1-y0+1)*0.10))
        region=np.zeros_like(grown)
        cv2.rectangle(region,(max(4,x0-px),max(4,y0-py)),
                      (min(gray.shape[1]-5,x1+px),min(int(gray.shape[0]*.84)-2,y1+py)),255,-1)
        grown=cv2.bitwise_and(grown,region)

    # Keep the connected physical body, smooth small holes, and reject tiny fragments.
    grown=cv2.bitwise_or(grown,core)
    grown=cv2.morphologyEx(grown,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9)))
    n,lab,stats,_=cv2.connectedComponentsWithStats(grown)
    clean=np.zeros_like(grown)
    core_count=cv2.countNonZero(core)
    for i in range(1,n):
        area=int(stats[i,cv2.CC_STAT_AREA])
        comp=(lab==i).astype(np.uint8)*255
        if area>=max(20,int(core_count*0.18)) and cv2.countNonZero(cv2.bitwise_and(comp,cv2.dilate(core,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)))))>0:
            clean[lab==i]=255

    # Final polarity/contrast guard. This removes smooth shadow halos that can be
    # connected to a residue but have weak physical contrast.
    if cv2.countNonZero(clean)>0:
        cvals=signal[clean>0]
        if float(np.median(cvals)) < max(0.8, weak*0.80):
            clean=core.copy()

    # Coherence guard: a physical residue should be one main body (with only very
    # nearby fragments). Do not let dozens of SEM grain speckles survive as one ROI.
    n2,lab2,st2,_=cv2.connectedComponentsWithStats(clean)
    if n2>1:
        largest=max(range(1,n2),key=lambda i:int(st2[i,cv2.CC_STAT_AREA]))
        keep=np.zeros_like(clean)
        keep[lab2==largest]=255
        lx,ly,lw,lh=[int(st2[largest,j]) for j in range(4)]
        lcx=lx+lw/2; lcy=ly+lh/2; ldiag=max(float(np.hypot(lw,lh)),10.0)
        largest_area=max(int(st2[largest,cv2.CC_STAT_AREA]),1)
        for i in range(1,n2):
            if i==largest: continue
            area=int(st2[i,cv2.CC_STAT_AREA])
            if area<max(12,int(largest_area*0.025)): continue
            x,y,ww,hh=[int(st2[i,j]) for j in range(4)]
            gapx=max(lx-x, x-(lx+lw), 0); gapy=max(ly-y, y-(ly+lh), 0)
            dist=np.hypot((x+ww/2)-lcx,(y+hh/2)-lcy)
            if (gapx<=max(5,int(ldiag*.08)) and gapy<=max(5,int(ldiag*.08))) or dist<=ldiag*.22:
                keep[lab2==i]=255
        clean=keep
    return clean if cv2.countNonZero(clean)>=max(20,int(core_count*0.45)) else core

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

def residue_features(sem_ref,c_map,o_map,n_map,si_map,ai_boxes=None,se_map=None):
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
        c["mask"]=refine_candidate_mask(sem_ref,c["mask"],c.get("polarity"))
        ys,xs=np.where(c["mask"]>0)
        if len(xs):
            c["bbox"]=(int(xs.min()),int(ys.min()),int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1))
            c["area"]=int(len(xs)); c["cx"]=float((xs.min()+xs.max())/2); c["cy"]=float((ys.min()+ys.max())/2); c["ww"]=float(c["bbox"][2]); c["hh"]=float(c["bbox"][3])
    candidates=[c for c in candidates if cv2.countNonZero(c["mask"])>=20]
    registration=estimate_sem_to_eds_registration(sem_ref,se_map)
    h,w=sem_ref.shape[:2]
    gray=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY)
    am=analytical_mask(gray.shape)>0
    gmed=float(np.median(gray[am])); gmad=float(np.median(np.abs(gray[am]-gmed))); gstd=max(1.4826*gmad,1.0)
    scored=[]
    for c in candidates:
        # Candidate pixels themselves are used for the signal comparison; no local ring.
        cmask=c["mask"]
        roi_c=map_sem_roi_to_element(cmask,c_map,se_map,registration)
        roi_o=map_sem_roi_to_element(cmask,o_map,se_map,registration)
        cm=global_roi_metrics(c_map,roi_c,"C"); om=global_roi_metrics(o_map,roi_o,"O")
        candidate_area=int(cv2.countNonZero(cmask)); vals=gray[cmask>0].astype(np.float32)
        morph_z=(float(np.median(vals))-gmed)/gstd if vals.size else 0.0
        # Absolute local contrast allows both bright and dark residues, while area/fill
        # prevents isolated background grain from becoming a high-ranked candidate.
        local_bg=cv2.GaussianBlur(gray,(0,0),25)
        delta_local=gray.astype(np.float32)-local_bg
        local_vals=delta_local[cmask>0]
        local_contrast=float(np.median(np.abs(local_vals))) if local_vals.size else 0.0
        # A residue should have a real interior signal, not merely a clean-looking edge
        # around a substrate feature.  Measure the eroded interior separately so a thin
        # outline/rim cannot score like a solid physical residue.
        eroded=cv2.erode(cmask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)),iterations=1)
        interior_vals=delta_local[eroded>0]
        interior_abs=float(abs(np.median(interior_vals))) if interior_vals.size else 0.0
        interior_score=float(sigmoid((interior_abs-3.0)/2.0)) if interior_vals.size else 0.0
        local_strength=float(np.clip(local_contrast/14.0,0,1))
        area_fraction=candidate_area/max(int(np.count_nonzero(am)),1)
        morphology_score=float(np.clip(
            .38*sigmoid((abs(morph_z)-.90)/1.10)+
            .24*interior_score+
            .20*np.clip(c["score"],0,1)+
            .12*local_strength+
            .06*np.clip(np.sqrt(area_fraction)*16,0,1),0,1))
        spatial=float(np.clip(min(cm["coverage"],om["coverage"])/55.0,0,1))
        # Selection is intentionally SEM-first. C/O are supporting evidence only.
        # A tiny bright speck can have a strong EDS signal; it must not outrank a
        # visually obvious physical residue simply because its C/O ratio is high.
        ai_bonus=0.0  # OpenAI is not authoritative for point-level ROI/classification.
        max_area_seen=max([int(x.get("area",0)) for x in candidates] or [1])
        rel_area=float(np.clip(np.sqrt(max(int(c.get("area",0))/max_area_seen,0.0)),0,1))
        size_bonus=0.10*rel_area
        visual_selection=float(np.clip(.50*morphology_score+.10*rel_area+size_bonus,0,1))
        selection=float(np.clip(.60*visual_selection+.18*cm["score"]+.15*om["score"]+.07*spatial+ai_bonus,0,1))
        # When a much larger, coherent SEM object exists, strongly penalize a tiny
        # candidate unless the larger object has essentially no physical contrast.
        if max_area_seen>0 and int(c.get("area",0)) < max_area_seen*0.12 and morphology_score<0.78:
            selection*=0.72
        scored.append((selection,c,cm,om,morphology_score,morph_z,spatial))
    if not scored:
        roi=np.zeros((h,w),np.uint8)
        return {"result":"Ambiguous","confidence":"Low","residue_score":0.0,"c_enrichment":0.0,"o_enrichment":0.0,
                "c_log2_ratio":0.0,"o_log2_ratio":0.0,"c_zscore":0.0,"o_zscore":0.0,"c_score":0.0,"o_score":0.0,
                "c_coverage":0.0,"o_coverage":0.0,"c_spatial_overlap":0.0,"o_spatial_overlap":0.0,"spatial_overlap":0.0,
                "morphology_score":0.0,"cluster_score":0.0,"roi_area_px":0,"candidate_area_px":0,"candidate_coverage":0.0,
                "review_reasons":["no_reliable_SEM_candidate"],"classification_note":"No defensible SEM candidate was detected; point requires human review."},roi

    scored.sort(key=lambda z:z[0],reverse=True)
    # Final SEM sanity check: if the top-ranked candidate is a tiny speck but a
    # substantially larger coherent object has a reasonable morphology score, use
    # the larger object. This prevents the old "small red mark far from the residue"
    # failure mode seen in Verification.
    top=scored[0]
    top_area=max(int(top[1].get("area",0)),1)
    visual_alts=[z for z in scored[1:] if int(z[1].get("area",0))>=top_area*2.5 and z[4]>=max(0.48,top[4]*0.72)]
    if visual_alts:
        alt=max(visual_alts,key=lambda z:(int(z[1].get("area",0))*max(z[4],0.01),z[4]))
        if alt[0] >= top[0]*0.72 or top_area < max(int(sem_ref.shape[0]*sem_ref.shape[1]*0.0012),25):
            scored.remove(alt)
            scored.insert(0,alt)
    best_score=scored[0][0]
    # Select one primary physical residue. Fragmented pieces belonging to that residue
    # are merged earlier; separate distant particles should not dilute the main ROI.
    item=scored[0]
    selected=[{"sel":item[0],"c":item[1],"cm":item[2],"om":item[3],"ms":item[4],"mz":item[5],"sp":item[6],"cx":item[1]["cx"],"cy":item[1]["cy"],"ww":item[1]["ww"],"hh":item[1]["hh"]}]

    roi=np.zeros((h,w),np.uint8)
    for d in selected: roi=cv2.bitwise_or(roi,d["c"]["mask"])
    # Recalculate element metrics on the final irregular ROI.
    rr_c,c_panel_info=map_sem_roi_to_element(roi,c_map,se_map,registration,return_info=True)
    rr_o,o_panel_info=map_sem_roi_to_element(roi,o_map,se_map,registration,return_info=True)
    rr_n,n_panel_info=map_sem_roi_to_element(roi,n_map,se_map,registration,return_info=True)
    rr_si,si_panel_info=map_sem_roi_to_element(roi,si_map,se_map,registration,return_info=True)
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
    # v23.7.48 element-specific lab rule:
    #   - C >= 2.40x AND O >= 3.00x -> Residue
    #   - C >= 2.00x AND O >= 2.00x, but either residue gate unmet -> Ambiguous
    #   - otherwise -> Non-residue
    # SEM morphology and a single-channel increase cannot override this rule.
    c_ratio=(cm["roi_mean"]/cm["global_mean"]) if cm["global_mean"] else 0.0
    o_ratio=(om["roi_mean"]/om["global_mean"]) if om["global_mean"] else 0.0
    c_dual=float(np.clip((c_ratio-MODERATE_RATIO)/(C_RESIDUE_RATIO-MODERATE_RATIO),0,1))
    o_dual=float(np.clip((o_ratio-MODERATE_RATIO)/(O_RESIDUE_RATIO-MODERATE_RATIO),0,1))
    dual_element_support=float(min(c_dual,o_dual))
    both_strong=(c_ratio>=C_RESIDUE_RATIO and o_ratio>=O_RESIDUE_RATIO)
    both_moderate=(c_ratio>=MODERATE_RATIO and o_ratio>=MODERATE_RATIO)
    # Keep the old channel scores visible as diagnostics, but do not let one
    # channel create a high residue score by itself.
    element_support=float(np.clip(.50*ce+.50*oe,0,1))
    gray_final=cv2.cvtColor(sem_ref,cv2.COLOR_BGR2GRAY).astype(np.float32)
    bg_final=cv2.GaussianBlur(gray_final,(0,0),25)
    d_final=gray_final-bg_final
    rvals=d_final[roi>0]
    sem_contrast=float(abs(np.median(rvals))) if rvals.size else 0.0
    sem_contrast_score=float(sigmoid((sem_contrast-3.5)/2.0)) if rvals.size else 0.0
    er_final=cv2.erode(roi,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)),iterations=1)
    ivals=d_final[er_final>0]
    sem_interior_score=float(sigmoid((abs(np.median(ivals))-3.0)/2.0)) if ivals.size else 0.0
    visual_evidence=float(np.clip(.55*morph+.30*sem_interior_score+.15*sem_contrast_score,0,1))
    # Score each element against its own residue gate, then use the weaker one.
    # This keeps the visible score bands consistent with classification:
    # C 2.40x / O 3.00x is exactly the 70-point Residue boundary.
    c_rule_score=element_ratio_score(c_ratio,C_RESIDUE_RATIO)
    o_rule_score=element_ratio_score(o_ratio,O_RESIDUE_RATIO)
    score=float(min(c_rule_score,o_rule_score))
    raw_score=score
    residue_gate=bool(both_strong)
    result=co_rule_result(c_ratio,o_ratio)
    if result=="Residue":
        margin=score-0.70
        conf="High" if margin>=.20 else "Medium" if margin>=.10 else "Low"
    elif result=="Non-residue":
        margin=0.60-score
        conf="High" if margin>=.20 else "Medium" if margin>=.10 else "Low"
    else:
        conf="Low" if score<0.70 else "Medium"
    reasons=[]
    if not both_strong: reasons.append("C_below_2.40x_or_O_below_3.00x")
    if c_ratio<C_RESIDUE_RATIO: reasons.append("C_ratio_below_2.40x")
    if o_ratio<O_RESIDUE_RATIO: reasons.append("O_ratio_below_3.00x")
    if ce<.55 or oe<.55: reasons.append("weak_C_or_O_global_evidence")
    if abs(ce-oe)>.30: reasons.append("C_O_disagreement")
    if spatial<.08: reasons.append("weak_C_O_high_signal_overlap")
    if rq["roi_quality"]<.55: reasons.append("low_roi_quality_or_partial_mask")
    if score>=.70 and rq["roi_quality"]<.55: reasons.append("high_score_but_roi_quality_low")
    if 0.60<=score<0.70: reasons.append("score_in_ambiguous_band")
    roi_area=int(cv2.countNonZero(roi)); candidate_area=sum(int(cv2.countNonZero(d["c"]["mask"])) for d in selected)
    ys,xs=np.where(roi>0); box_area=0 if len(xs)==0 else max(1,(xs.max()-xs.min()+1)*(ys.max()-ys.min()+1))
    return {
        "result":result,"confidence":conf,"residue_score":round(score,4),"score_calibrated":True,"raw_residue_score":round(raw_score,4),"score_calibration_method":"v23.7.48_element_specific_C2.4_O3.0","score_calibration_c_2_4x_is_70":True,"score_calibration_o_3x_is_70":True,"score_calibration_20x_is_100":True,"visual_evidence_bonus":0.0,"sem_contrast":round(sem_contrast,3),"sem_contrast_score":round(sem_contrast_score,4),"sem_interior_score":round(sem_interior_score,4),"visual_evidence":round(visual_evidence,4),"element_support":round(element_support,4),
        "c_enrichment":cm["contrast_pct"],"o_enrichment":om["contrast_pct"],"c_log2_ratio":cm["log2_ratio"],"o_log2_ratio":om["log2_ratio"],
        "c_zscore":cm["zscore"],"o_zscore":om["zscore"],"c_score":ce,"o_score":oe,
        "c_roi_global_ratio":round(c_ratio,4),"n_roi_global_ratio":round((nm["roi_mean"]/nm["global_mean"]) if nm["global_mean"] else 0.0,4),"o_roi_global_ratio":round(o_ratio,4),
        "c_dual_evidence":round(c_dual,4),"o_dual_evidence":round(o_dual,4),"dual_element_support":round(dual_element_support,4),
        "both_c_o_strong":bool(both_strong),"both_c_o_moderate":bool(both_moderate),
        "c_coverage":cm["coverage"],"o_coverage":om["coverage"],"c_spatial_overlap":cm["overlap"],"o_spatial_overlap":om["overlap"],
        "spatial_overlap":round(spatial*100,2),"n_enrichment":nm["contrast_pct"],"n_log2_ratio":nm["log2_ratio"],"n_zscore":nm["zscore"],"n_coverage":nm["coverage"],
        "si_enrichment":sm["contrast_pct"],"si_zscore":sm["zscore"],"morphology_score":round(float(morph),4),"cluster_score":round(spatial,4),
        "roi_area_px":roi_area,"candidate_area_px":candidate_area,"candidate_coverage":round(candidate_area/max(box_area,1)*100,2),
        "roi_quality":rq["roi_quality"],"roi_fill_ratio":rq["roi_fill_ratio"],"roi_component_count":rq["roi_component_count"],"roi_bbox_area_px":rq["roi_bbox_area_px"],
        "sem_global_median":round(gmed,3),"sem_global_mad":round(gmad,3),"sem_candidate_zscore":round(float(np.mean([d["mz"] for d in selected])),3),
        "c_roi_mean":cm["roi_mean"],"c_global_mean":cm["global_mean"],"c_global_std":cm["global_std"],
        "o_roi_mean":om["roi_mean"],"o_global_mean":om["global_mean"],"o_global_std":om["global_std"],
        "n_roi_mean":nm["roi_mean"],"n_global_mean":nm["global_mean"],"n_global_std":nm["global_std"],
        "selected_candidate_rank":1,"candidate_count":len(scored),"selected_candidate_count":len(selected),"candidate_selection_score":round(best_score,4),
        "review_reasons":reasons,
        "n_note":"N is a diagnostic global-vs-ROI comparison and is not part of the current residue score.",
        "si_note":"Si is a diagnostic global-vs-ROI comparison and is not part of the current residue score.",
        "classification_note":"v23.7.48 rule: Residue requires C ROI/Global >=2.40x AND O ROI/Global >=3.00x. If both are >=2.00x but either residue gate is unmet, the result is Ambiguous. If either is below 2.00x, the result is Non-residue. SEM morphology and spatial overlap are diagnostics only and cannot override the C/O rule. The whole analytical image is the baseline after excluding the bottom metadata/scale-bar region; Local Ring is not used.","c_residue_ratio_threshold":C_RESIDUE_RATIO,"o_residue_ratio_threshold":O_RESIDUE_RATIO,
        "sem_to_eds_registration":registration,
        "element_panel_registration":{"C":c_panel_info,"O":o_panel_info,"N":n_panel_info,"Si":si_panel_info},
        "roi_registration_version":"v23.9.5-auto-layout-shared-page2"
    },roi


def polygon_to_mask(shape, polygon):
    """Create an analytical ROI mask from one polygon or multiple normalized polygons."""
    h,w=shape[:2]
    mask=np.zeros((h,w),np.uint8)
    if not isinstance(polygon,list) or not polygon:
        return mask
    # Backward compatible: [{x,y}, ...] is one polygon; [[{x,y},...], ...] is multiple regions.
    if polygon and isinstance(polygon[0],dict):
        polygons=[polygon]
    else:
        polygons=polygon
    for poly in polygons:
        if not isinstance(poly,list) or len(poly)<3:
            continue
        pts=[]
        for item in poly:
            try:
                if isinstance(item,dict): x=float(item.get("x")); y=float(item.get("y"))
                else: x=float(item[0]); y=float(item[1])
                if not np.isfinite(x) or not np.isfinite(y): continue
                pts.append([int(np.clip(round(x*(w-1)),0,w-1)),int(np.clip(round(y*(h-1)),0,h-1))])
            except Exception:
                continue
        if len(pts)>=3:
            cv2.fillPoly(mask,[np.asarray(pts,np.int32)],255)
    # Human ROI coordinates are drawn against the full SEM image shown in the
    # Verification editor.  The previous 0.84 analytical cutoff removed the
    # lowest ~16% of the image, which could clip legitimate residues near the
    # bottom of the analytical field.  Keep only the actual bottom metadata/
    # scale-bar band out of Human ROI calculations.
    mask[~(analytical_mask((h,w), footer_fraction=0.92)>0)]=0
    return mask


def ai_boxes_to_mask(shape, boxes):
    h,w=shape[:2]; mask=np.zeros((h,w),np.uint8)
    for b in boxes or []:
        try:
            x=float(b.get("x",0)); y=float(b.get("y",0)); bw=float(b.get("w",0)); bh=float(b.get("h",0))
            x0=int(np.clip(x*w,0,w-1)); y0=int(np.clip(y*h,0,h-1)); x1=int(np.clip((x+bw)*w,0,w)); y1=int(np.clip((y+bh)*h,0,h))
            if x1>x0 and y1>y0: cv2.rectangle(mask,(x0,y0),(x1-1,y1-1),255,-1)
        except Exception: continue
    mask[~(analytical_mask((h,w))>0)]=0
    return mask


def human_roi_features(sem_ref,c_map,o_map,n_map,si_map,roi,se_map=None):
    """Recalculate element evidence on a user-confirmed ROI without changing auto CV results."""
    roi=(roi>0).astype(np.uint8)*255
    registration=estimate_sem_to_eds_registration(sem_ref,se_map)
    c_roi,c_map_info=map_sem_roi_to_element(roi,c_map,se_map,registration,return_info=True)
    o_roi,o_map_info=map_sem_roi_to_element(roi,o_map,se_map,registration,return_info=True)
    n_roi,n_map_info=map_sem_roi_to_element(roi,n_map,se_map,registration,return_info=True)
    si_roi,si_map_info=map_sem_roi_to_element(roi,si_map,se_map,registration,return_info=True)
    cm=global_roi_metrics(c_map,c_roi,"C")
    om=global_roi_metrics(o_map,o_roi,"O")
    nm=global_roi_metrics(n_map,n_roi,"N")
    sm=global_roi_metrics(si_map,si_roi,"Si")
    rq=roi_quality_metrics(sem_ref,roi,1)
    cr=(cm["roi_mean"]/cm["global_mean"]) if cm["global_mean"] else 0.0
    orat=(om["roi_mean"]/om["global_mean"]) if om["global_mean"] else 0.0
    # Human ROI uses the same element-specific rule/calibration as automatic analysis.
    # C 2.40x and O 3.00x are the respective 70-point residue boundaries.
    both=cr>=C_RESIDUE_RATIO and orat>=O_RESIDUE_RATIO
    moderate=cr>=MODERATE_RATIO and orat>=MODERATE_RATIO
    human_score=round(float(np.clip(min(
        element_ratio_score(cr,C_RESIDUE_RATIO),
        element_ratio_score(orat,O_RESIDUE_RATIO),
    )*100.0,0,100)),1)
    return {
        "human_roi_area_px":int(cv2.countNonZero(roi)),
        "human_roi_quality":rq["roi_quality"],"human_roi_fill_ratio":rq["roi_fill_ratio"],"human_roi_component_count":rq["roi_component_count"],
        "human_c_roi_mean":cm["roi_mean"],"human_c_global_mean":cm["global_mean"],"human_c_ratio":round(cr,4),"human_c_zscore":cm["zscore"],"human_c_coverage":cm["coverage"],
        "human_o_roi_mean":om["roi_mean"],"human_o_global_mean":om["global_mean"],"human_o_ratio":round(orat,4),"human_o_zscore":om["zscore"],"human_o_coverage":om["coverage"],
        "human_n_roi_mean":nm["roi_mean"],"human_n_global_mean":nm["global_mean"],"human_n_ratio":round((nm["roi_mean"]/nm["global_mean"]) if nm["global_mean"] else 0.0,4),
        "human_si_ratio":round((sm["roi_mean"]/sm["global_mean"]) if sm["global_mean"] else 0.0,4),
        "human_roi_both_strong":bool(both),"human_roi_both_moderate":bool(moderate),
        "human_residue_score":human_score,
        "human_roi_rule_result":"Residue" if both else ("Ambiguous" if moderate else "Non-residue"),
        "human_score_rule":"C >=2.40x and O >=3.00x -> Residue; both >=2.00x but either residue gate unmet -> Ambiguous; otherwise Non-residue",
        "human_sem_to_eds_registration":registration,
        "human_element_panel_registration":{"C":c_map_info,"O":o_map_info,"N":n_map_info,"Si":si_map_info},
        "human_roi_registration_version":"v23.9.5-auto-layout-shared-page2",
        "c_residue_ratio_threshold":C_RESIDUE_RATIO,"o_residue_ratio_threshold":O_RESIDUE_RATIO,
    }

def run(payload):
    outdir=Path(payload["output_dir"]); outdir.mkdir(parents=True,exist_ok=True); paths={}
    # v23.9.10 normally receives one tiny 3-page mini PDF. Reuse the open
    # document instead of reopening it for Page 1/2/3; this also remains correct
    # for the original multi-file fallback path.
    open_docs={}
    try:
        for local_no,info in enumerate(payload["pages"],1):
            src=str(Path(info["path"]))
            doc=open_docs.get(src)
            if doc is None:
                doc=fitz.open(src); open_docs[src]=doc
            page=doc.load_page(int(info["index"])); data=render_page_to_jpeg(page); out=outdir/f"page_{local_no}.jpg"; out.write_bytes(data); paths[f"page_{local_no}"]=str(out); del data,page
            gc.collect()
    finally:
        for doc in open_docs.values():
            try: doc.close()
            except Exception: pass
        open_docs.clear(); gc.collect()
    first=cv2.imread(paths["page_1"],cv2.IMREAD_COLOR); second=cv2.imread(paths["page_2"],cv2.IMREAD_COLOR)
    if first is None or second is None: raise RuntimeError("Unable to read rendered source pages")
    p1=crop_page1_layout(first); p2=crop_page2_element_maps(second); del first,second; gc.collect()
    features,roi=residue_features(p1["sem"],p2["c_map"],p2["o_map"],p2["n_map"],p2["si_map"],payload.get("ai_roi_boxes"),se_map=p2["se_map"])
    raw={"sem":p1["sem"],"eds_map":p1["eds_map"],"full_element_maps_original":p2["full_element_maps_original"],"se_map":p2["se_map"],"c_map":p2["c_map"],"n_map":p2["n_map"],"o_map":p2["o_map"],"si_map":p2["si_map"]}
    for key,im in raw.items():
        fp=outdir/f"{key}.jpg"; save_crop(im,fp); paths[key]=str(fp)
    roi_sem=roi
    fp=outdir/"sem_residue_overlay.jpg"; save_crop(make_box_overlay(p1["sem"],roi_sem,roi_color=(0,0,255)),fp,92); paths["sem_residue_overlay"]=str(fp)
    # Backward-compatible asset name: it now contains the contour-only overlay.
    fp=outdir/"sem_roi_ring_overlay.jpg"; save_crop(make_box_overlay(p1["sem"],roi_sem,roi_color=(0,0,255)),fp,92); paths["sem_roi_ring_overlay"]=str(fp)
    registration=features.get("sem_to_eds_registration") or estimate_sem_to_eds_registration(p1["sem"],p2["se_map"])
    roi_eds=map_sem_roi_to_element(roi,p2["c_map"],p2["se_map"],registration)
    fp=outdir/"eds_co_overlay.jpg"; save_crop(make_co_overlay(p2["c_map"],p2["o_map"],roi_eds),fp,92); paths["eds_co_overlay"]=str(fp)
    fp=outdir/"element_maps_enhanced.jpg"; save_crop(make_element_overview(p2,roi_eds),fp,92); paths["element_maps_enhanced"]=str(fp)
    for label,key in [("SE","se_map"),("C","c_map"),("N","n_map"),("O","o_map"),("Si","si_map")]:
        rr=map_sem_roi_to_element(roi,p2[key],p2["se_map"],registration)
        # Backward-compatible asset name; yellow ring is no longer drawn.
        fp=outdir/f"{key}_roi_ring.jpg"; save_crop(make_box_overlay(p2[key],rr,roi_color=(255,255,255)),fp,92); paths[f"{key}_roi_ring"]=str(fp)
        if label!="SE":
            enh=enhance_element_map(p2[key],label); fp=outdir/f"{key}_enhanced_overlay.jpg"; save_crop(make_box_overlay(enh,rr,roi_color=(255,255,255)),fp,92); paths[f"{key}_enhanced_overlay"]=str(fp)
    p1_layout=p1.get("_layout") or {}; p2_layout=p2.get("_layout") or {}
    features.update({
        "substrate_type":payload.get("substrate_type","SiCN"),"ai_roi_guided": bool(payload.get("ai_roi_boxes")), "ai_roi_box_count": len(payload.get("ai_roi_boxes") or []),
        "map_parser":"Bruker auto-layout Page-1 SEM/EDS + Page-2 SE/C/N/O/Si panels; ROI detected from Page-1 SEM",
        "pdf_layout_version":"v23.9.5-auto-panel-detection",
        "pdf_layout_profile":p2_layout.get("profile") or "legacy_fallback",
        "pdf_layout_detection":{"page1":p1_layout.get("method"),"page2":p2_layout.get("method")},
        "page1_sem_crop_box":p1_layout.get("sem_box"),"page1_eds_crop_box":p1_layout.get("eds_box"),
        "page2_panel_boxes":p2_layout.get("boxes"),"page2_full_box":p2_layout.get("full_box"),
        "page":payload["page"],"cv_version":"v21.6-visual-first-shadow-suppression-interior-aware-score",
        "viewer_note":"ROI is detected from the Page-1 SEM as an irregular residue mask. v23.9.5 auto-detects the actual Bruker image-panel geometry before ROI/EDS mapping, supporting both standard and tall export layouts without condition-specific crop offsets. Red/white contours show the actual ROI. No Local Ring is displayed or used for new analysis. Score remains 0-100 with the existing piecewise calibration."
    })
    del roi,p1,p2,raw; gc.collect()
    rec={"id":payload["id"],"power":payload["power"],"time":payload["time"],"wafer":payload["wafer"],"point":payload["point"],"zone":payload["zone"],"substrate_type":payload.get("substrate_type","SiCN"),"condition":payload["condition"],"page":payload["page"],"pages_per_point":payload["pages_per_point"],"source_pages":payload["source_pages"],"assets":paths,"features":features}
    Path(payload["result_path"]).write_text(json.dumps(rec,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: python point_worker.py <point_input.json>")
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
