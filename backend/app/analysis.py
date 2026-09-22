import base64, re, gc
from pathlib import Path
import cv2
import pymupdf as fitz
import numpy as np

ZONE_MAP = {1:"Center",2:"Center",3:"Center",4:"Edge",5:"Diamond",6:"Edge",7:"Edge",8:"Center",9:"Diamond"}


def parse_int_list(value):
    """Parse values such as '1,4,5,9', '1-9', or '1,4-6,9'."""
    if isinstance(value, list):
        return [int(x) for x in value]
    text = str(value or "").replace("~", "-").replace("–", "-").replace(" ", "")
    out = []
    for token in text.split(","):
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            if a.isdigit() and b.isdigit():
                out.extend(range(int(a), int(b) + 1))
        elif token.isdigit():
            out.append(int(token))
    return sorted(set(out))


def normalize_conditions(conditions):
    """Normalize UI condition rows into a strict sequential mapping."""
    normalized = []
    for i, c in enumerate(conditions or [], 1):
        power = int(re.search(r"\d+", str(c.get("power", ""))).group()) if re.search(r"\d+", str(c.get("power", ""))) else 0
        time = int(re.search(r"\d+", str(c.get("time", ""))).group()) if re.search(r"\d+", str(c.get("time", ""))) else 0
        wafers = parse_int_list(c.get("wafers", c.get("wafer", "")))
        points = parse_int_list(c.get("points", "1-9"))
        if not power or not time or not wafers or not points:
            raise ValueError(f"Condition {i} has invalid Power / Time / Wafer / Point settings.")
        normalized.append({"index": i, "power": power, "time": time, "wafers": wafers, "points": points})
    if not normalized:
        raise ValueError("At least one analysis condition is required.")
    return normalized


def condition_point_sequence(conditions):
    """Create the exact Point sequence implied by the user-entered conditions."""
    seq = []
    for c in conditions:
        for wafer in c["wafers"]:
            for point in c["points"]:
                seq.append({
                    "condition": c["index"],
                    "power": c["power"],
                    "time": c["time"],
                    "wafer": wafer,
                    "point": point,
                    "zone": ZONE_MAP.get(point, "Unknown"),
                })
    return seq


def render_page(page, max_dim=1800):
    """Render one page with a hard pixel-size cap to avoid RAM spikes."""
    rect = page.rect
    base_w = max(float(rect.width), 1.0)
    base_h = max(float(rect.height), 1.0)
    scale = min(1.25, float(max_dim) / max(base_w, base_h))
    scale = max(scale, 0.35)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    # Own the bytes so the Pixmap can be released immediately.
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
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
    cv2.imwrite(str(path), img)


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


def extract_pdfs(pdf_paths, output_dir, conditions, pages_per_point=3, progress_callback=None):
    """Read PDFs in upload order and map every N pages to one Point.

    Memory-conscious implementation: source PDFs are opened one at a time and
    pages are rendered only for the current Point. The Point metadata is driven
    entirely by the user-entered condition sequence.
    """
    if pages_per_point < 1:
        raise ValueError("pages_per_point must be at least 1")
    conditions = normalize_conditions(conditions)
    sequence = condition_point_sequence(conditions)

    pdf_paths = [Path(p) for p in pdf_paths]
    file_counts = []
    total_pages = 0
    for path in pdf_paths:
        with fitz.open(path) as doc:
            count = len(doc)
        file_counts.append(count)
        total_pages += count

    expected_pages = len(sequence) * pages_per_point
    if total_pages != expected_pages:
        raise ValueError(
            f"Page count mismatch: expected {expected_pages} pages "
            f"({len(sequence)} points × {pages_per_point} pages/point), "
            f"but received {total_pages} pages. Check condition order/count."
        )

    # Build only lightweight page-location metadata; do not keep PDF Page
    # objects or rendered images for the entire document in memory.
    page_locations = []
    for path, count in zip(pdf_paths, file_counts):
        page_locations.extend((path, i) for i in range(count))

    records = []
    open_path = None
    open_doc = None
    try:
        for point_index, meta in enumerate(sequence):
            start = point_index * pages_per_point
            group = page_locations[start:start + pages_per_point]
            power = f"{meta['power']}W"
            time = f"{meta['time']}s"
            point_id = f"{power}_{time}_W{meta['wafer']}_P{meta['point']}"
            pdir = output_dir / point_id
            pdir.mkdir(parents=True, exist_ok=True)
            paths = {}
            source_pages = []
            first_page_path = None

            # IMPORTANT: never keep all three full-page images in RAM.
            # Render -> save -> release one page at a time.
            for local_no, (source_path, page_index) in enumerate(group, 1):
                if open_doc is None or open_path != source_path:
                    if open_doc is not None:
                        open_doc.close()
                        open_doc = None
                    open_doc = fitz.open(source_path)
                    open_path = source_path
                page = open_doc.load_page(page_index)
                img = render_page(page)
                source_pages.append({"file": source_path.name, "page": page_index + 1})
                fp = pdir / f"page_{local_no}.jpg"
                save_crop(img, fp)
                paths[f"page_{local_no}"] = str(fp)
                if local_no == 1:
                    first_page_path = fp
                del img
                del page
                gc.collect()

            # Generate compatibility assets only from the first source page.
            # Source page_1..page_N remain untouched for exact inspection.
            feat = {}
            if first_page_path and first_page_path.exists():
                first_img = cv2.imread(str(first_page_path), cv2.IMREAD_COLOR)
                if first_img is not None:
                    c0 = crop_layout(first_img)
                    for key, crop in c0.items():
                        fp = pdir / f"{key}.jpg"
                        save_crop(crop, fp)
                        paths[key] = str(fp)
                    feat = residue_features(c0["sem"], c0["element_maps"], c0["element_maps"])
                    del c0
                    del first_img
                else:
                    feat = residue_features(np.zeros((100, 100), dtype=np.uint8))
            else:
                feat = residue_features(np.zeros((100, 100), dtype=np.uint8))
            gc.collect()

            rec = {
                "id": point_id,
                "power": power,
                "time": time,
                "wafer": meta["wafer"],
                "point": meta["point"],
                "zone": meta["zone"],
                "condition": meta["condition"],
                "page": {"start": start + 1, "end": start + pages_per_point},
                "pages_per_point": pages_per_point,
                "source_pages": source_pages,
                "assets": paths,
                "features": feat,
            }
            records.append(rec)
            if progress_callback:
                progress_callback(point_index + 1, len(sequence), "point_analysis")

            # Explicitly release temporary objects before moving to the next Point.
            gc.collect()

        return records
    finally:
        if open_doc is not None:
            open_doc.close()


def extract_pdf(pdf_path: Path, output_dir: Path, conditions=None, pages_per_point=3):
    """Backward-compatible single-PDF wrapper."""
    if conditions is None:
        # Legacy fallback is intentionally explicit: one condition is inferred
        # only when the caller already supplies an analyzable naming convention.
        doc = fitz.open(pdf_path)
        pages = len(doc)
        doc.close()
        raise ValueError(
            "Condition settings are required for this EDS format. "
            f"The uploaded PDF has {pages} pages; enter Power/Time/Wafer/Point settings first."
        )
    return extract_pdfs([pdf_path], output_dir, conditions, pages_per_point)


def image_to_data_url(path):
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower().replace(".", "") or "jpeg"
    return f"data:image/{'jpeg' if ext == 'jpg' else ext};base64,{base64.b64encode(data).decode()}"
