"""Memory-minimal one-Point PDF/SEM/OpenCV worker for v9.

This process intentionally exits after one Point so native PyMuPDF/OpenCV memory
cannot accumulate across hundreds of Points inside the FastAPI process.
"""
import gc
import json
import os
import sys
from pathlib import Path

# Keep native numerical libraries from creating large thread pools.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("OPENCV_OPENCL_RUNTIME", "disabled")

import cv2
import numpy as np
import pymupdf as fitz

cv2.setNumThreads(1)
try:
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass


def render_page_to_jpeg(page, quality=92):
    """Render directly to JPEG bytes without a full-page NumPy copy.

    Matrix 1.0 is intentional: v9 does not downsample the PDF for analysis.
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    try:
        data = pix.tobytes("jpeg", jpg_quality=quality)
    finally:
        del pix
    return data


def crop_page1_layout(img):
    """Crop the SEM image and the large composite EDS map from vendor page 1."""
    h, w = img.shape[:2]
    return {
        "sem": img[int(.10*h):int(.40*h), int(.07*w):int(.59*w)],
        "eds_map": img[int(.45*h):int(.90*h), int(.07*w):int(.97*w)],
    }


def crop_page2_element_maps(img):
    """Extract the vendor's individual C/N/O/Si maps from page 2.

    This layout matches the Bruker EDS PDF used for the current dataset:
    C = upper-right, N = middle-left, O = middle-right, Si = lower-left.
    """
    h, w = img.shape[:2]
    return {
        "element_maps": img[int(.09*h):int(.79*h), int(.06*w):int(.96*w)],
        "c_map": img[int(.09*h):int(.31*h), int(.52*w):int(.96*w)],
        "n_map": img[int(.32*h):int(.55*h), int(.06*w):int(.50*w)],
        "o_map": img[int(.32*h):int(.55*h), int(.52*w):int(.96*w)],
        "si_map": img[int(.55*h):int(.79*h), int(.06*w):int(.50*w)],
    }

def save_crop(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # JPEG keeps the stored working set much smaller than PNG.
    cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])


def residue_features(sem, c_map=None, o_map=None, n_map=None):
    gray = cv2.cvtColor(sem, cv2.COLOR_BGR2GRAY) if len(sem.shape) == 3 else sem
    bg = cv2.GaussianBlur(gray, (0, 0), 9)
    top = cv2.normalize(cv2.absdiff(gray, bg), None, 0, 255, cv2.NORM_MINMAX)
    _, mask = cv2.threshold(top, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    roi = np.zeros_like(mask)
    if n > 1:
        idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        roi[labels == idx] = 255
    else:
        roi = mask
    roi_area = max(int((roi > 0).sum()), 1)
    coverage = roi_area / max(mask.size, 1)

    def enrich(im):
        if im is None:
            return 0.0, 0.0
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) if len(im.shape) == 3 else im
        if g.shape != roi.shape:
            g = cv2.resize(g, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_AREA)
        inside = g[roi > 0].astype(float)
        outside = g[roi == 0].astype(float)
        if len(inside) == 0 or len(outside) == 0:
            return 0.0, 0.0
        base = max(float(outside.mean()), 1.0)
        return float(np.clip((inside.mean() - base) / base, -1, 3)), float((inside > np.percentile(g, 70)).mean())

    ce, cc = enrich(c_map)
    oe, oc = enrich(o_map)
    ne, nc = enrich(n_map)
    morphology_score = float(np.clip(coverage * 8 + np.std(top) / 255.0, 0, 1))
    cscore = float(np.clip((ce + 1) / 2, 0, 1))
    oscore = float(np.clip((oe + 1) / 2, 0, 1))
    cluster = float(np.clip(.45*morphology_score + .275*cscore + .275*oscore, 0, 1))
    residue_score = float(np.clip(.45*cscore + .35*oscore + .20*morphology_score, 0, 1))
    if residue_score >= .62:
        result = "Residue"
    elif residue_score <= .38:
        result = "Non-residue"
    else:
        result = "Review"
    confidence = "High" if abs(residue_score-.5) >= .25 else "Medium" if abs(residue_score-.5) >= .12 else "Low"
    return {
        "result": result,
        "confidence": confidence,
        "residue_score": round(residue_score, 4),
        "c_enrichment": round(ce*100, 2),
        "o_enrichment": round(oe*100, 2),
        "c_coverage": round(cc*100, 2),
        "o_coverage": round(oc*100, 2),
        "n_enrichment": round(ne*100, 2),
        "n_coverage": round(nc*100, 2),
        "cluster_score": round(cluster, 4),
        "roi_area_px": roi_area,
        "candidate_coverage": round(coverage*100, 3),
    }


def run(payload):
    output_dir = Path(payload["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    # Render one source page at a time. The PDF pixmap is encoded directly to
    # JPEG bytes, so there is no full-page NumPy array just for file output.
    for local_no, page_info in enumerate(payload["pages"], 1):
        source_path = Path(page_info["path"])
        page_index = int(page_info["index"])
        with fitz.open(source_path) as doc:
            page = doc.load_page(page_index)
            jpeg_bytes = render_page_to_jpeg(page)
            out = output_dir / f"page_{local_no}.jpg"
            out.write_bytes(jpeg_bytes)
            paths[f"page_{local_no}"] = str(out)
            del jpeg_bytes
            del page
        gc.collect()

    # Page 1 contains the SEM and composite EDS map. Page 2 contains the
    # individual C/N/O/Si elemental maps. Keep only one rendered page in RAM at a time.
    first_img = cv2.imread(paths["page_1"], cv2.IMREAD_COLOR)
    if first_img is None:
        raise RuntimeError("Unable to read rendered page_1.jpg")
    page1_crops = crop_page1_layout(first_img)
    del first_img
    gc.collect()

    second_img = cv2.imread(paths["page_2"], cv2.IMREAD_COLOR)
    if second_img is None:
        raise RuntimeError("Unable to read rendered page_2.jpg")
    page2_crops = crop_page2_element_maps(second_img)
    del second_img
    gc.collect()

    crops = {**page1_crops, **page2_crops}
    for key, crop in crops.items():
        fp = output_dir / f"{key}.jpg"
        save_crop(crop, fp)
        paths[key] = str(fp)

    # C/O maps are now extracted from their individual vendor map panels.
    # Pixel brightness is treated as a relative map signal, not as direct concentration.
    features = residue_features(page1_crops["sem"], page2_crops["c_map"], page2_crops["o_map"], page2_crops["n_map"])
    features["n_note"] = "N map extracted and stored as a relative signal; N is not used in the current residue score."
    features["map_parser"] = "Bruker page-2 individual C/N/O/Si map crop"
    features["page"] = record_page = payload["page"]
    features["cv_note"] = (
        "C and O signals are extracted from their individual vendor EDS maps. "
        "Map brightness is used only as a relative signal; it is not treated as direct concentration."
    )

    for key in list(crops):
        crops[key] = None
    page1_crops.clear(); page2_crops.clear(); crops.clear()
    del crops
    gc.collect()

    record = {
        "id": payload["id"],
        "power": payload["power"],
        "time": payload["time"],
        "wafer": payload["wafer"],
        "point": payload["point"],
        "zone": payload["zone"],
        "condition": payload["condition"],
        "page": payload["page"],
        "pages_per_point": payload["pages_per_point"],
        "source_pages": payload["source_pages"],
        "assets": paths,
        "features": features,
    }
    Path(payload["result_path"]).write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python point_worker.py <point_input.json>")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    run(payload)
