"""Isolated one-Point PDF/SEM/OpenCV worker for v8.

This process intentionally exits after one Point so native PyMuPDF/OpenCV memory
cannot accumulate across hundreds of Points inside the FastAPI process.
"""
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


def render_page(page, max_dim=1400):
    rect = page.rect
    base_w = max(float(rect.width), 1.0)
    base_h = max(float(rect.height), 1.0)
    scale = min(1.0, float(max_dim) / max(base_w, base_h))
    scale = max(scale, 0.30)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    try:
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
    finally:
        del pix
    return arr


def crop_layout(img):
    h, w = img.shape[:2]
    return {
        "sem": img[int(.10*h):int(.40*h), int(.02*w):int(.49*w)],
        "spectrum": img[int(.10*h):int(.40*h), int(.51*w):int(.98*w)],
        "eds_map": img[int(.42*h):int(.70*h), int(.02*w):int(.49*w)],
        "element_maps": img[int(.42*h):int(.70*h), int(.51*w):int(.98*w)],
        "data": img[int(.72*h):int(.97*h), int(.02*w):int(.60*w)],
        "result": img[int(.72*h):int(.97*h), int(.62*w):int(.98*w)],
    }


def save_crop(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # JPEG keeps the stored working set much smaller than PNG.
    cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])


def residue_features(sem, c_map=None, o_map=None):
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
        "cluster_score": round(cluster, 4),
        "roi_area_px": roi_area,
        "candidate_coverage": round(coverage*100, 3),
    }


def run(payload):
    output_dir = Path(payload["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    source_pages = payload["source_pages"]

    # Open only the source PDF needed for this page, render one page at a time,
    # then close it immediately. The largest temporary array is never retained
    # alongside the next page's full render.
    for local_no, page_info in enumerate(payload["pages"], 1):
        source_path = Path(page_info["path"])
        page_index = int(page_info["index"])
        with fitz.open(source_path) as doc:
            page = doc.load_page(page_index)
            img = render_page(page)
            fp = output_dir / f"page_{local_no}.jpg"
            save_crop(img, fp)
            paths[f"page_{local_no}"] = str(fp)
            del img
            del page

    # Compatibility crops / preliminary CV features use only page 1.
    first_path = Path(paths["page_1"])
    first_img = cv2.imread(str(first_path), cv2.IMREAD_COLOR)
    if first_img is None:
        raise RuntimeError("Unable to read rendered page_1.jpg")
    crops = crop_layout(first_img)
    for key, crop in crops.items():
        fp = output_dir / f"{key}.jpg"
        save_crop(crop, fp)
        paths[key] = str(fp)

    # IMPORTANT SCIENTIFIC LIMITATION: the current PDF crop is a combined
    # Element Maps panel. It is not a validated separate C-map/O-map extraction.
    # Keep this as a preliminary CV signal until vendor map exports are parsed.
    features = residue_features(crops["sem"], crops["element_maps"], crops["element_maps"])
    features["cv_note"] = "Preliminary CV only; C/O maps are not separately parsed from the vendor PDF in v8."

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
        "source_pages": source_pages,
        "assets": paths,
        "features": features,
    }
    Path(payload["result_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python point_worker.py <point_input.json>")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    run(payload)
