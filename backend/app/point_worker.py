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

    # Decode only page 1 for the current preliminary CV/crop stage.
    # Pages 2 and 3 stay on disk and are not held in RAM.
    first_img = cv2.imread(paths["page_1"], cv2.IMREAD_COLOR)
    if first_img is None:
        raise RuntimeError("Unable to read rendered page_1.jpg")
    crops = crop_layout(first_img)
    del first_img
    gc.collect()

    for key, crop in crops.items():
        fp = output_dir / f"{key}.jpg"
        save_crop(crop, fp)
        paths[key] = str(fp)

    # SCIENTIFIC LIMITATION: the vendor PDF's combined Element Maps panel is
    # not yet parsed into separate validated C and O maps. Until that parser is
    # implemented, the combined panel is used only as a preliminary proxy.
    features = residue_features(crops["sem"], crops["element_maps"], crops["element_maps"])
    features["cv_note"] = (
        "Preliminary CV only; C/O maps are not separately parsed from the vendor PDF in v9. "
        "The current C/O signals are a proxy from the combined Element Maps panel."
    )

    for key in list(crops):
        crops[key] = None
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
