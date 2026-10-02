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


def extract_pdfs(pdf_paths, output_dir, conditions, pages_per_point=3, progress_callback=None, roi_reference_examples=None):
    """Analyze one Point per isolated subprocess.

    v8 deliberately does NOT keep PyMuPDF/OpenCV objects in the FastAPI process.
    Each Point launches `point_worker.py`, which opens only the required PDF pages,
    renders/saves them, extracts features, writes one JSON result, and exits.
    The OS therefore reclaims native PDF/OpenCV memory after every Point.
    """
    if pages_per_point < 1:
        raise ValueError("pages_per_point must be at least 1")
    conditions = normalize_conditions(conditions)
    sequence = condition_point_sequence(conditions)
    pdf_paths = [Path(p) for p in pdf_paths]
    locations, total_pages = _build_page_locations(pdf_paths)
    expected_pages = len(sequence) * pages_per_point
    if total_pages != expected_pages:
        raise ValueError(
            f"Page count mismatch: expected {expected_pages} pages "
            f"({len(sequence)} points × {pages_per_point} pages/point), "
            f"but received {total_pages} pages. Check condition order/count."
        )

    worker = Path(__file__).with_name("point_worker.py")
    records = []
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Limit numerical-library thread fan-out in every child process.
    child_env = {
        **__import__("os").environ,
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "OPENCV_OPENCL_RUNTIME": "disabled",
    }

    for point_index, meta in enumerate(sequence):
        start = point_index * pages_per_point
        group = locations[start:start + pages_per_point]
        power = f"{meta['power']}W"
        time = f"{meta['time']}s"
        point_id = f"{power}_{time}_W{meta['wafer']}_P{meta['point']}"
        pdir = output_dir / point_id
        pdir.mkdir(parents=True, exist_ok=True)
        result_path = pdir / "_point_result.json"

        payload = {
            "id": point_id,
            "power": power,
            "time": time,
            "wafer": meta["wafer"],
            "point": meta["point"],
            "zone": meta["zone"],
            "condition": meta["condition"],
            "page": {"start": start + 1, "end": start + pages_per_point},
            "pages_per_point": pages_per_point,
            "source_pages": [{"file": Path(src).name, "page": page_index + 1} for src, page_index in group],
            "output_dir": str(pdir),
            "result_path": str(result_path),
            "pages": [{"path": src, "index": page_index} for src, page_index in group],
            "roi_reference_examples": roi_reference_examples or [],
        }
        payload_path = pdir / "_point_input.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        try:
            proc = subprocess.run(
                [sys.executable, str(worker), str(payload_path)],
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=240,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "point worker failed").strip()
                raise RuntimeError(f"Point {point_index + 1}/{len(sequence)} failed: {detail[-4000:]}")
            if not result_path.exists():
                raise RuntimeError(f"Point {point_index + 1}/{len(sequence)} worker finished without a result file.")
            record = json.loads(result_path.read_text(encoding="utf-8"))
            # Hybrid ROI stage: OpenAI proposes coarse visual boxes only for uncertain
            # points; OpenCV remains responsible for the actual irregular pixel ROI and
            # the final Residue/Non-residue/Review classification.
            try:
                from .ai import ai_available, analyze_roi_boxes_with_openai
                ai_mode = __import__("os").getenv("OPENAI_ROI_MODE", "all").lower()
                f = record.get("features") or {}
                uncertain = (
                    f.get("result") == "Review" or
                    f.get("confidence") == "Low" or
                    f.get("selected_candidate_count", 0) == 0 or
                    f.get("candidate_coverage", 100) < 18 or
                    (f.get("morphology_score", 1) < 0.46 and f.get("result") != "Non-residue")
                )
                use_ai = ai_available() and ai_mode in {"assist", "all"} and (ai_mode == "all" or uncertain)
                if use_ai and record.get("assets", {}).get("sem"):
                    ai_hint = analyze_roi_boxes_with_openai(record["assets"]["sem"], f, payload.get("roi_reference_examples") or [])
                    boxes = ai_hint.get("boxes") or []
                    if boxes:
                        payload["ai_roi_boxes"] = boxes
                        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                        ai_proc = subprocess.run(
                            [sys.executable, str(worker), str(payload_path)],
                            env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=240, check=False,
                        )
                        if ai_proc.returncode == 0 and result_path.exists():
                            refined = json.loads(result_path.read_text(encoding="utf-8"))
                            refined.setdefault("features", {})["ai_roi_used"] = True
                            refined["features"]["ai_roi_box_count"] = len(boxes)
                            refined["features"]["ai_roi_model"] = ai_hint.get("model") or __import__("os").getenv("OPENAI_ROI_MODEL", __import__("os").getenv("OPENAI_MODEL", "gpt-5.6-luna"))
                            refined["features"]["ai_roi_notes"] = ai_hint.get("notes", "")
                            refined["features"]["ground_truth_reference_count"] = len(payload.get("roi_reference_examples") or [])
                            refined["features"]["ground_truth_roi_learning"] = bool(payload.get("roi_reference_examples"))
                            refined["features"]["ai_roi_boxes"] = boxes
                            record = refined
                        else:
                            record.setdefault("features", {})["ai_roi_used"] = False
                            record["features"]["ai_roi_error"] = (ai_proc.stderr or ai_proc.stdout or "AI refinement failed")[-1000:]
                    else:
                        record.setdefault("features", {})["ai_roi_used"] = False
            except Exception as ai_exc:
                record.setdefault("features", {})["ai_roi_used"] = False
                record["features"]["ai_roi_error"] = f"{type(ai_exc).__name__}: {ai_exc}"
            payload.pop("ai_roi_boxes", None)
            records.append(record)
            if progress_callback:
                progress_callback(point_index + 1, len(sequence), "point_analysis")
        finally:
            # Only the tiny control JSON is removed. Images are retained for R2/report export.
            try:
                payload_path.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                result_path.unlink(missing_ok=True)
            except Exception:
                pass
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
