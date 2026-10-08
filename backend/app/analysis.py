import os
# Keep the API parent and Point worker within Render's memory budget.
os.environ.setdefault("MALLOC_ARENA_MAX", "2")
os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "131072")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("OPENCV_OPENCL_RUNTIME", "disabled")
import base64
import json
import re
import subprocess
import sys
import gc
from pathlib import Path

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
        power_match = re.search(r"\d+", str(c.get("power", "")))
        time_match = re.search(r"\d+", str(c.get("time", "")))
        power = int(power_match.group()) if power_match else 0
        time = int(time_match.group()) if time_match else 0
        wafers = parse_int_list(c.get("wafers", c.get("wafer", "")))
        points = parse_int_list(c.get("points", "1-9"))
        if not power or not time or not wafers or not points:
            raise ValueError(f"Condition {i} has invalid Power / Time / Wafer / Point settings.")
        normalized.append({"index": i, "power": power, "time": time, "wafers": wafers, "points": points})
    if not normalized:
        raise ValueError("At least one analysis condition is required.")
    return normalized


def condition_point_sequence(conditions, position_substrates=None):
    """Create the exact Point sequence implied by the user-entered conditions.

    position_substrates maps point/position number to the physical substrate used at
    that location. It is metadata only; the existing CV/EDS ratio calculation remains
    unchanged. Missing positions default to SiCN for backward compatibility.
    """
    position_substrates = {str(k): str(v) for k, v in (position_substrates or {}).items()}
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
                    "substrate_type": position_substrates.get(str(point), "SiCN"),
                })
    return seq


def _page_counts(pdf_paths):
    # Heavy PyMuPDF import is deliberately local so the API parent process stays small.
    import pymupdf as fitz
    counts = []
    for path in pdf_paths:
        with fitz.open(path) as doc:
            counts.append(len(doc))
    return counts


def _build_page_locations(pdf_paths):
    counts = _page_counts(pdf_paths)
    locations = []
    for path, count in zip(pdf_paths, counts):
        locations.extend((str(path), i) for i in range(count))
    return locations, sum(counts)


def _extract_point_number(page_text):
    """Extract the Point number printed on an EDS PDF page.

    The PDF is allowed to have missing/duplicated whole Points, so page count is
    intentionally not used as the source of truth.  We only accept explicit Point
    labels and avoid generic page-number matches.
    """
    text = re.sub(r"\s+", " ", page_text or "")
    patterns = [
        r"\bPoint\s*(?:No\.?|#|:)??\s*(\d{1,3})\b",
        r"\bP\s*(?:No\.?|#|:)\s*(\d{1,3})\b",
        r"\bPOINT\s*(?:NO\.?|#|:)??\s*(\d{1,3})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1))
    return None


def _build_point_groups(pdf_paths, pages_per_point=3):
    """Return consecutive page groups keyed by the Point label in the PDF.

    A normal Point is three pages.  If the same Point appears again immediately,
    it becomes a separate group and the caller can discard it as a duplicate.
    Missing Points simply produce no group.  If a page has no label, the most
    recently observed label is carried forward (typical for page 2/3 of a Point).
    """
    import pymupdf as fitz
    groups = []
    current = None
    for path in pdf_paths:
        with fitz.open(path) as doc:
            for idx in range(len(doc)):
                try:
                    n = _extract_point_number(doc.load_page(idx).get_text("text"))
                except Exception:
                    n = None
                if n is not None and (current is None or n != current["point_number"]):
                    current = {"point_number": n, "pages": []}
                    groups.append(current)
                if current is not None:
                    current["pages"].append((str(path), idx))
    # Keep only complete Point groups. If the PDF is image-only (the common Bruker
    # export has no searchable text), Point labels cannot be extracted from PDF text.
    # In that case fall back to the original fixed pages-per-point grouping so a
    # normal 3-page Point PDF remains analyzable. This fallback deliberately does not
    # claim to detect missing/duplicated Points; without a readable Point label there
    # is no reliable way to distinguish those cases from page order alone.
    valid = [g for g in groups if len(g["pages"]) >= pages_per_point]
    for g in valid:
        g["pages"] = g["pages"][:pages_per_point]
    if valid:
        return valid

    locations, _ = _build_page_locations(pdf_paths)
    fallback = []
    for start in range(0, len(locations), pages_per_point):
        chunk = locations[start:start + pages_per_point]
        if len(chunk) < pages_per_point:
            break
        fallback.append({
            "point_number": None,
            "pages": chunk,
            "fallback_index": start // pages_per_point,
        })
    return fallback


def extract_pdfs(pdf_paths, output_dir, conditions, pages_per_point=3, progress_callback=None, roi_reference_examples=None, position_substrates=None):
    """Analyze available Points without requiring an exact PDF page count.

    Whole-Point duplicates that occur consecutively are skipped. Missing Points are
    not fabricated and are simply absent from the returned records. The sequence is
    aligned by the Point number printed in the PDF, so a missing Point does not shift
    every subsequent Point by three pages.
    """
    if pages_per_point < 1:
        raise ValueError("pages_per_point must be at least 1")
    conditions = normalize_conditions(conditions)
    sequence = condition_point_sequence(conditions, position_substrates)
    pdf_paths = [Path(p) for p in pdf_paths]
    groups = _build_point_groups(pdf_paths, pages_per_point)
    if not groups:
        raise ValueError("No analyzable Point groups were found in the PDF.")

    # Align observed Point labels to the next matching expected Point. This allows
    # P4 to be absent while P5 still maps to P5, and supports repeated wafer/condition
    # sequences where Point numbers restart at 1.
    aligned = []
    expected_idx = 0
    previous_observed = None
    for group in groups:
        observed = group.get("point_number")
        if observed is None:
            # Image-only PDF fallback: preserve the original sequential mapping.
            # The fallback is safe for normal exports and avoids the old fatal
            # "No analyzable Point groups" error. Missing/duplicate whole Points
            # cannot be identified reliably without a readable Point label.
            if expected_idx >= len(sequence):
                break
            match = expected_idx
        else:
            observed = int(observed)
            if previous_observed == observed:
                # Consecutive same-label group = duplicated whole Point. Skip it.
                continue
            match = None
            for j in range(expected_idx, len(sequence)):
                if int(sequence[j]["point"]) == observed:
                    match = j
                    break
            if match is None:
                # Unknown/out-of-sequence Point: do not guess a condition/wafer.
                continue
        meta = sequence[match]
        aligned.append((match, meta, group["pages"]))
        expected_idx = match + 1
        previous_observed = observed

    if not aligned:
        raise ValueError("No PDF Points matched the configured Point sequence.")

    worker = Path(__file__).with_name("point_worker.py")
    records = []
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    child_env = {
        **__import__("os").environ,
        "OMP_NUM_THREADS":"1", "OPENBLAS_NUM_THREADS":"1", "MKL_NUM_THREADS":"1",
        "NUMEXPR_NUM_THREADS":"1", "VECLIB_MAXIMUM_THREADS":"1", "OPENCV_OPENCL_RUNTIME":"disabled",
    }

    for done_index, (sequence_index, meta, group) in enumerate(aligned, 1):
        power=f"{meta['power']}W"; time=f"{meta['time']}s"
        point_id=f"{power}_{time}_W{meta['wafer']}_P{meta['point']}"
        pdir=output_dir/point_id; pdir.mkdir(parents=True,exist_ok=True)
        result_path=pdir/'_point_result.json'
        payload={"id":point_id,"power":power,"time":time,"wafer":meta['wafer'],"point":meta['point'],"zone":meta['zone'],"substrate_type":meta.get('substrate_type','SiCN'),"condition":meta['condition'],"page":{"start":group[0][1]+1,"end":group[-1][1]+1},"pages_per_point":pages_per_point,"source_pages":[{"file":Path(src).name,"page":page_index+1} for src,page_index in group],"output_dir":str(pdir),"result_path":str(result_path),"pages":[{"path":src,"index":page_index} for src,page_index in group],"roi_reference_examples":roi_reference_examples or []}
        payload_path=pdir/'_point_input.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        try:
            proc=subprocess.run([sys.executable,str(worker),str(payload_path)],env=child_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=240,check=False)
            if proc.returncode!=0:
                detail=(proc.stderr or proc.stdout or 'point worker failed').strip(); raise RuntimeError(f"Point {meta['point']} failed: {detail[-4000:]}")
            if not result_path.exists(): raise RuntimeError(f"Point {meta['point']} worker finished without a result file.")
            record=json.loads(result_path.read_text(encoding='utf-8'))
            try:
                from .ai import ai_available, analyze_roi_boxes_with_openai
                ai_mode=__import__('os').getenv('OPENAI_ROI_MODE','all').lower(); f=record.get('features') or {}
                uncertain=(f.get('result')=='Review' or f.get('confidence')=='Low' or f.get('selected_candidate_count',0)==0 or f.get('candidate_coverage',100)<18 or (f.get('morphology_score',1)<0.46 and f.get('result')!='Non-residue'))
                use_ai=ai_available() and ai_mode in {'assist','all'} and (ai_mode=='all' or uncertain)
                if use_ai and record.get('assets',{}).get('sem'):
                    ai_hint=analyze_roi_boxes_with_openai(record['assets']['sem'],f,payload.get('roi_reference_examples') or []); boxes=ai_hint.get('boxes') or []
                    if boxes:
                        payload['ai_roi_boxes']=boxes; payload_path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                        ai_proc=subprocess.run([sys.executable,str(worker),str(payload_path)],env=child_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=240,check=False)
                        if ai_proc.returncode==0 and result_path.exists():
                            refined=json.loads(result_path.read_text(encoding='utf-8')); refined.setdefault('features',{})['ai_roi_used']=True; refined['features']['ai_roi_box_count']=len(boxes); refined['features']['ai_roi_model']=ai_hint.get('model') or __import__('os').getenv('OPENAI_ROI_MODEL',__import__('os').getenv('OPENAI_MODEL','gpt-5.6-luna')); refined['features']['ai_roi_notes']=ai_hint.get('notes',''); refined['features']['ground_truth_reference_count']=len(payload.get('roi_reference_examples') or []); refined['features']['ground_truth_roi_learning']=bool(payload.get('roi_reference_examples')); refined['features']['ai_roi_boxes']=boxes; record=refined
                        else: record.setdefault('features',{})['ai_roi_used']=False
                    else: record.setdefault('features',{})['ai_roi_used']=False
            except Exception as ai_exc:
                record.setdefault('features',{})['ai_roi_used']=False; record['features']['ai_roi_error']=f'{type(ai_exc).__name__}: {ai_exc}'
            payload.pop('ai_roi_boxes',None); records.append(record)
            if progress_callback: progress_callback(done_index,len(aligned),'point_analysis')
        finally:
            try: payload_path.unlink(missing_ok=True)
            except Exception: pass
            try: result_path.unlink(missing_ok=True)
            except Exception: pass
            gc.collect()
    return records

def extract_pdf(pdf_path: Path, output_dir: Path, conditions=None, pages_per_point=3):
    if conditions is None:
        raise ValueError("Condition settings are required for this EDS format. Enter Power/Time/Wafer/Point settings first.")
    return extract_pdfs([pdf_path], output_dir, conditions, pages_per_point)


def image_to_data_url(path):
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower().replace(".", "") or "jpeg"
    return f"data:image/{'jpeg' if ext == 'jpg' else ext};base64,{base64.b64encode(data).decode()}"
