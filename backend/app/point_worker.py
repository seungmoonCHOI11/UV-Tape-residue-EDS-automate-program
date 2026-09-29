"""One-Point PDF -> SEM/EDS crop + residue ROI analysis worker.

Design rules:
- PDF CROP coordinates are fixed to the vendor page layout (scaled to the
  actual rendered page size). These are image extraction coordinates, not ROI.
- Residue ROI is detected independently from each Point's SEM crop.
- The detected SEM ROI is projected by normalized coordinates to the SE/C/N/O/Si
  panels, so the same physical field is compared across maps.
- Local Ring is used as the background reference for C/O evidence.
- Final CV result is binary: Residue / Non-residue. Confidence is separate.
- OpenAI is not used for point-level classification.
"""
import gc
import json
import os
import sys
from pathlib import Path

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

# Coordinates measured from the actual 1240 x 1754 vendor pages used in the
# current dataset. They are deliberately pixel-based for PDF CROP, then scaled
# if a PDF page is rendered at another size.
PAGE1_BASE = (1240, 1754)
PAGE1_CROPS = {
    "sem": (98, 207, 628, 418),
    "eds_map": (101, 818, 1102, 734),
}
PAGE2_BASE = (1240, 1754)
PAGE2_CROPS = {
    "se_map": (101, 190, 542, 362),
    "c_map": (658, 190, 542, 362),
    "n_map": (101, 566, 542, 362),
    "o_map": (658, 566, 542, 362),
    "si_map": (101, 943, 542, 362),
}


def render_page_to_jpeg(page, quality=92):
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    try:
        data = pix.tobytes("jpeg", jpg_quality=quality)
    finally:
        del pix
    return data


def crop_fixed(img, rect, base_size):
    """Pixel-coordinate crop, scaled from the measured vendor page size."""
    bw, bh = base_size
    h, w = img.shape[:2]
    x, y, cw, ch = rect
    sx, sy = w / bw, h / bh
    x0 = max(0, min(w - 1, int(round(x * sx))))
    y0 = max(0, min(h - 1, int(round(y * sy))))
    x1 = max(x0 + 1, min(w, int(round((x + cw) * sx))))
    y1 = max(y0 + 1, min(h, int(round((y + ch) * sy))))
    return img[y0:y1, x0:x1].copy()


def save_crop(img, path, quality=90):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])


def _draw_box(img, box, color, thickness=3):
    x, y, w, h = [int(v) for v in box]
    out = img.copy()
    cv2.rectangle(out, (x, y), (x + w, y + h), color, thickness, lineType=cv2.LINE_AA)
    return out


def project_box(norm_box, shape):
    h, w = shape[:2]
    nx, ny, nw, nh = norm_box
    x = int(round(nx * w)); y = int(round(ny * h))
    rw = int(round(nw * w)); rh = int(round(nh * h))
    x = max(0, min(w - 2, x)); y = max(0, min(h - 2, y))
    rw = max(2, min(w - x, rw)); rh = max(2, min(h - y, rh))
    return x, y, rw, rh


def _valid_mask(shape, bottom_exclusion=0.12):
    h, w = shape[:2]
    valid = np.ones((h, w), np.uint8)
    valid[int(h * (1 - bottom_exclusion)):, :] = 0
    return valid


def detect_residue_roi(sem):
    """Detect a residue candidate from the SEM image itself.

    The algorithm looks for locally bright/contrasting connected objects in the
    usable SEM field, filters out tiny noise, then merges nearby pieces belonging
    to the same visible residue. No fixed ROI coordinates are used.
    """
    gray = cv2.cvtColor(sem, cv2.COLOR_BGR2GRAY) if sem.ndim == 3 else sem.copy()
    h, w = gray.shape[:2]
    usable_h = max(10, int(h * 0.88))  # exclude scale-bar / acquisition text area
    work = gray[:usable_h, :]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(work)
    background = cv2.GaussianBlur(clahe, (0, 0), 10)
    top_hat = cv2.subtract(clahe, background)

    # High local-contrast objects are candidates. Percentile + robust spread keeps
    # the threshold adaptive to different SEM brightness levels.
    p98 = float(np.percentile(top_hat, 98.0))
    med = float(np.median(top_hat))
    mad = float(np.median(np.abs(top_hat - med)))
    robust_threshold = med + 3.5 * 1.4826 * mad
    threshold = max(p98, robust_threshold)
    mask = (top_hat >= threshold).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates = []
    min_area = max(20, int(0.00005 * work.size))
    max_area = int(0.08 * work.size)
    for i in range(1, n):
        x, y, cw, ch, area = [int(v) for v in stats[i]]
        if min_area <= area <= max_area:
            candidates.append({"i": i, "x": x, "y": y, "w": cw, "h": ch, "area": area})
    candidates.sort(key=lambda c: c["area"], reverse=True)

    if not candidates:
        # No reliable candidate: return a tiny center ROI with low confidence so
        # the point remains reviewable rather than silently crashing.
        rw, rh = max(20, int(w * 0.08)), max(20, int(h * 0.08))
        x, y = (w - rw) // 2, (usable_h - rh) // 2
        return {
            "bbox": (x, y, rw, rh),
            "normalized_bbox": (x / w, y / h, rw / w, rh / h),
            "candidate_count": 0,
            "candidate_area_ratio": 0.0,
            "morphology_confidence": 0.0,
            "mask": np.zeros((h, w), np.uint8),
        }

    # Start from the strongest candidate and include spatially adjacent bright
    # fragments that are close enough to plausibly belong to the same residue.
    selected = [candidates[0]]
    for c in candidates[1:]:
        x0 = selected[0]["x"]
        y0 = selected[0]["y"]
        x1 = x0 + selected[0]["w"]
        y1 = y0 + selected[0]["h"]
        dx = max(x0 - c["x"], c["x"] + c["w"] - x1, 0)
        dy = max(y0 - c["y"], c["y"] + c["h"] - y1, 0)
        if dx <= max(35, int(0.06 * w)) and dy <= max(35, int(0.06 * h)):
            selected.append(c)
            # Update aggregate bounds so a chain of nearby pieces can merge.
            x0 = min(s["x"] for s in selected); y0 = min(s["y"] for s in selected)
            x1 = max(s["x"] + s["w"] for s in selected); y1 = max(s["y"] + s["h"] for s in selected)
            selected[0]["x"], selected[0]["y"] = x0, y0
            selected[0]["w"], selected[0]["h"] = x1 - x0, y1 - y0

    x0 = min(c["x"] for c in selected); y0 = min(c["y"] for c in selected)
    x1 = max(c["x"] + c["w"] for c in selected); y1 = max(c["y"] + c["h"] for c in selected)
    pad_x = max(12, int(0.03 * w)); pad_y = max(12, int(0.03 * h))
    x0 = max(0, x0 - pad_x); y0 = max(0, y0 - pad_y)
    x1 = min(w, x1 + pad_x); y1 = min(usable_h, y1 + pad_y)
    rw, rh = x1 - x0, y1 - y0

    roi_mask = np.zeros((h, w), np.uint8)
    for c in selected:
        roi_mask[:usable_h, :][labels == c.get("i", -1)] = 255

    candidate_area = float(sum(c["area"] for c in selected))
    roi_ratio = candidate_area / max(work.size, 1)
    # Morphology confidence is deliberately based on SEM evidence only.
    area_term = np.clip(np.log10(candidate_area + 1) / 4.0, 0, 1)
    contrast = float(np.mean(top_hat[roi_mask[:usable_h] > 0])) if np.any(roi_mask[:usable_h] > 0) else 0.0
    contrast_term = float(np.clip(contrast / 80.0, 0, 1))
    morphology_conf = float(np.clip(0.55 * area_term + 0.45 * contrast_term, 0, 1))

    return {
        "bbox": (x0, y0, rw, rh),
        "normalized_bbox": (x0 / w, y0 / h, rw / w, rh / h),
        "candidate_count": len(selected),
        "candidate_area_ratio": roi_ratio,
        "morphology_confidence": morphology_conf,
        "mask": roi_mask,
    }


def local_map_metrics(img, norm_box):
    """Compare ROI against a local ring; signal is relative map intensity only."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img.copy()
    h, w = gray.shape[:2]
    x, y, rw, rh = project_box(norm_box, gray.shape)
    roi = np.zeros((h, w), np.uint8)
    roi[y:y + rh, x:x + rw] = 1
    dilated = cv2.dilate(roi, np.ones((31, 31), np.uint8), iterations=1)
    ring = ((dilated > 0) & (roi == 0) & (_valid_mask(gray.shape) > 0))
    # Exclude the map's bottom acquisition labels/scale-bar region from both
    # background and coverage statistics.
    valid = _valid_mask(gray.shape) > 0
    inside = gray[(roi > 0) & valid].astype(float)
    outside = gray[ring].astype(float)
    if len(inside) == 0 or len(outside) < 20:
        return {"roi_box": (x, y, rw, rh), "background": 0, "roi_median": 0, "contrast_pct": 0, "z_score": 0, "coverage_pct": 0}
    bg_median = float(np.median(outside))
    roi_median = float(np.median(inside))
    mad = float(np.median(np.abs(outside - bg_median)))
    robust_sd = max(1.4826 * mad, 1.0)
    z = (roi_median - bg_median) / robust_sd
    p90 = float(np.percentile(gray[valid], 90))
    coverage = float(np.mean(inside >= p90) * 100.0)
    contrast = (roi_median - bg_median) / max(abs(bg_median), 1.0) * 100.0
    return {
        "roi_box": (x, y, rw, rh),
        "background": round(bg_median, 4),
        "roi_median": round(roi_median, 4),
        "contrast_pct": round(float(contrast), 4),
        "z_score": round(float(z), 4),
        "coverage_pct": round(coverage, 4),
    }


def _evidence(metric, z_min, contrast_min):
    return bool(metric["z_score"] >= z_min and metric["contrast_pct"] >= contrast_min)


def classify_residue(morphology_conf, c, o):
    """Binary point classification from SEM residue + local C/O evidence.

    The SEM is the spatial gate: there must first be a visible residue candidate.
    Tape-residue evidence then requires C and O enrichment at that same ROI.
    Confidence records how strongly the evidence agrees; it does not create a
    third user-facing class.
    """
    sem_strong = morphology_conf >= 0.42
    c_strong = _evidence(c, 0.45, 20.0)
    o_strong = _evidence(o, 0.20, 12.0)
    c_support = _evidence(c, 0.20, 8.0)
    o_support = _evidence(o, 0.05, 5.0)

    if sem_strong and c_strong and o_strong:
        result = "Residue"
        confidence = "High"
    elif sem_strong and c_strong and o_support:
        result = "Residue"
        confidence = "Medium"
    elif sem_strong and c_support and o_strong:
        result = "Residue"
        confidence = "Medium"
    else:
        result = "Non-residue"
        if sem_strong and (c_support or o_support):
            confidence = "Low"
        elif sem_strong:
            confidence = "Medium"
        else:
            confidence = "High" if not (c_support or o_support) else "Low"

    score = (
        0.45 * float(np.clip(morphology_conf, 0, 1))
        + 0.30 * float(np.clip((c["z_score"] - 0.1) / 1.2, 0, 1))
        + 0.25 * float(np.clip((o["z_score"] - 0.05) / 0.8, 0, 1))
    )
    reasons = []
    if not sem_strong: reasons.append("SEM_residue_candidate_weak")
    if not c_strong: reasons.append("C_evidence_below_strong_threshold")
    if not o_strong: reasons.append("O_evidence_below_strong_threshold")
    return result, confidence, float(np.clip(score, 0, 1)), reasons


def make_overlay(img, box, color, ring=True):
    out = img.copy()
    x, y, rw, rh = [int(v) for v in box]
    # Local Ring is rendered as a yellow dashed rectangle around the ROI.
    if ring:
        pad = max(10, int(round(min(img.shape[:2]) * 0.035)))
        rx0, ry0 = max(0, x - pad), max(0, y - pad)
        rx1, ry1 = min(img.shape[1] - 1, x + rw + pad), min(img.shape[0] - 1, y + rh + pad)
        for xx in range(rx0, rx1, 14):
            cv2.line(out, (xx, ry0), (min(xx + 7, rx1), ry0), (0, 220, 255), 2, cv2.LINE_AA)
            cv2.line(out, (xx, ry1), (min(xx + 7, rx1), ry1), (0, 220, 255), 2, cv2.LINE_AA)
        for yy in range(ry0, ry1, 14):
            cv2.line(out, (rx0, yy), (rx0, min(yy + 7, ry1)), (0, 220, 255), 2, cv2.LINE_AA)
            cv2.line(out, (rx1, yy), (rx1, min(yy + 7, ry1)), (0, 220, 255), 2, cv2.LINE_AA)
    cv2.rectangle(out, (x, y), (x + rw, y + rh), color, 3, cv2.LINE_AA)
    return out


def run(payload):
    output_dir = Path(payload["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    for local_no, page_info in enumerate(payload["pages"], 1):
        source_path = Path(page_info["path"])
        page_index = int(page_info["index"])
        with fitz.open(source_path) as doc:
            page = doc.load_page(page_index)
            jpeg_bytes = render_page_to_jpeg(page)
            out = output_dir / f"page_{local_no}.jpg"
            out.write_bytes(jpeg_bytes)
            paths[f"page_{local_no}"] = str(out)
            del jpeg_bytes, page
        gc.collect()

    # Exact, fixed PDF CROP stage.
    p1 = cv2.imread(paths["page_1"], cv2.IMREAD_COLOR)
    p2 = cv2.imread(paths["page_2"], cv2.IMREAD_COLOR)
    if p1 is None or p2 is None:
        raise RuntimeError("Unable to read rendered PDF pages.")
    page1 = {k: crop_fixed(p1, r, PAGE1_BASE) for k, r in PAGE1_CROPS.items()}
    page2 = {k: crop_fixed(p2, r, PAGE2_BASE) for k, r in PAGE2_CROPS.items()}
    del p1, p2
    gc.collect()

    # SEM-driven dynamic ROI stage.
    detection = detect_residue_roi(page1["sem"])
    norm_box = detection["normalized_bbox"]
    sem_box = detection["bbox"]
    se_box = project_box(norm_box, page2["se_map"].shape)
    c_box = project_box(norm_box, page2["c_map"].shape)
    n_box = project_box(norm_box, page2["n_map"].shape)
    o_box = project_box(norm_box, page2["o_map"].shape)
    si_box = project_box(norm_box, page2["si_map"].shape)

    c_metrics = local_map_metrics(page2["c_map"], norm_box)
    o_metrics = local_map_metrics(page2["o_map"], norm_box)
    n_metrics = local_map_metrics(page2["n_map"], norm_box)
    si_metrics = local_map_metrics(page2["si_map"], norm_box)
    result, confidence, score, reasons = classify_residue(
        detection["morphology_confidence"], c_metrics, o_metrics
    )

    # Raw/source-derived assets.
    asset_defs = {
        "sem": page1["sem"],
        "eds_map": page1["eds_map"],
        "se_map": page2["se_map"],
        "c_map": page2["c_map"],
        "n_map": page2["n_map"],
        "o_map": page2["o_map"],
        "si_map": page2["si_map"],
    }
    for key, img in asset_defs.items():
        fp = output_dir / f"{key}.jpg"
        save_crop(img, fp)
        paths[key] = str(fp)

    # Viewer overlays. SEM = red ROI; SE/C/N/O/Si = white ROI + yellow Local Ring.
    overlays = {
        "sem_residue_overlay": make_overlay(page1["sem"], sem_box, (0, 0, 255), ring=False),
        "se_roi_overlay": make_overlay(page2["se_map"], se_box, (255, 255, 255), ring=True),
        "c_roi_overlay": make_overlay(page2["c_map"], c_box, (255, 255, 255), ring=True),
        "n_roi_overlay": make_overlay(page2["n_map"], n_box, (255, 255, 255), ring=True),
        "o_roi_overlay": make_overlay(page2["o_map"], o_box, (255, 255, 255), ring=True),
        "si_roi_overlay": make_overlay(page2["si_map"], si_box, (255, 255, 255), ring=True),
    }
    for key, img in overlays.items():
        fp = output_dir / f"{key}.jpg"
        save_crop(img, fp)
        paths[key] = str(fp)

    features = {
        "result": result,
        "confidence": confidence,
        "residue_score": round(score, 4),
        "roi": {
            "source": "SEM residue candidate detection",
            "normalized_bbox": [round(float(v), 6) for v in norm_box],
            "sem_bbox_px": list(map(int, sem_box)),
            "se_bbox_px": list(map(int, se_box)),
            "c_bbox_px": list(map(int, c_box)),
            "n_bbox_px": list(map(int, n_box)),
            "o_bbox_px": list(map(int, o_box)),
            "si_bbox_px": list(map(int, si_box)),
            "candidate_count": detection["candidate_count"],
            "candidate_area_ratio": round(float(detection["candidate_area_ratio"]), 6),
            "morphology_confidence": round(float(detection["morphology_confidence"]), 4),
        },
        "c_metrics": c_metrics,
        "o_metrics": o_metrics,
        "n_metrics": n_metrics,
        "si_metrics": si_metrics,
        "c_enrichment": round(float(c_metrics["contrast_pct"]), 2),
        "o_enrichment": round(float(o_metrics["contrast_pct"]), 2),
        "c_coverage": round(float(c_metrics["coverage_pct"]), 2),
        "o_coverage": round(float(o_metrics["coverage_pct"]), 2),
        "n_enrichment": round(float(n_metrics["contrast_pct"]), 2),
        "n_coverage": round(float(n_metrics["coverage_pct"]), 2),
        "cluster_score": round(float(detection["morphology_confidence"]), 4),
        "local_ring": "Applied",
        "review_reasons": reasons,
        "map_signal_note": "EDS map brightness is treated as a relative X-ray count/intensity signal, not direct concentration.",
        "crop_note": "PDF CROP uses fixed vendor-page pixel coordinates; ROI is detected dynamically from the SEM crop for each Point.",
        "classifier": "SEM residue candidate + local C/O evidence",
        "openai_point_classification": False,
        "page": {"start": payload["page"]["start"], "end": payload["page"]["end"]},
    }

    record = {
        "id": payload["id"], "power": payload["power"], "time": payload["time"],
        "wafer": payload["wafer"], "point": payload["point"], "zone": payload["zone"],
        "condition": payload["condition"], "page": payload["page"],
        "pages_per_point": payload["pages_per_point"], "source_pages": payload["source_pages"],
        "assets": paths, "features": features,
    }
    Path(payload["result_path"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python point_worker.py <point_input.json>")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    run(payload)
