
import os, json, uuid, shutil, mimetypes, threading, traceback, hashlib, re
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
import cv2, numpy as np
from dotenv import load_dotenv

from .analysis import extract_pdfs, normalize_conditions, condition_point_sequence
from .ai import ai_available
from .r2_storage import r2
from . import db

load_dotenv()
BASE=Path(__file__).resolve().parents[1]
UPLOAD=Path(os.getenv("UPLOAD_DIR",BASE/"data/uploads"))
OUTPUT=Path(os.getenv("OUTPUT_DIR",BASE/"data/outputs"))
UPLOAD.mkdir(parents=True,exist_ok=True); OUTPUT.mkdir(parents=True,exist_ok=True)

app=FastAPI(title="UV Tape Residue EDS API",version="18.0.0")
origins=[x.strip() for x in os.getenv("CORS_ORIGINS","http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

PROJECTS={}
RECORDS={}
JOBS={}
JOB_LOCK=threading.Lock()

def set_job(job_id, **updates):
    with JOB_LOCK:
        JOBS.setdefault(job_id, {}).update(updates)

def get_job(job_id):
    with JOB_LOCK:
        return dict(JOBS.get(job_id, {}))


def public_record(r: dict) -> dict:
    out=dict(r)
    features=out.get("features") or {}
    # Keep score/result/confidence both at the top level and inside features so
    # freshly analyzed in-memory records and Supabase-loaded records render identically.
    if out.get("residue_score") is None:
        out["residue_score"]=features.get("residue_score")
    if out.get("confidence") is None:
        out["confidence"]=features.get("confidence")
    if out.get("cv_result") is None:
        out["cv_result"]=features.get("result")
    out["assets"]={k:f"/api/assets/{quote(r['id'],safe='')}/{quote(k,safe='')}" for k in r.get("assets",{})}
    out.pop("r2_assets", None)
    return out

def db_record(project_id: str, row: dict, analysis_row: dict = None, asset_map: dict = None) -> dict:
    analysis = analysis_row if analysis_row is not None else (db.get_analysis(row["id"]) if db.configured() else None)
    assets = asset_map if asset_map is not None else ({a["asset_type"]: a["storage_path"] for a in db.get_assets(row["id"])} if db.configured() else {})
    features = (analysis or {}).get("features") or {}
    if analysis:
        features = {**features,
            "residue_score": analysis.get("residue_score") if analysis.get("residue_score") is not None else 0,
            "c_enrichment": analysis.get("c_enrichment") if analysis.get("c_enrichment") is not None else 0,
            "o_enrichment": analysis.get("o_enrichment") if analysis.get("o_enrichment") is not None else 0,
            "c_coverage": analysis.get("c_coverage") if analysis.get("c_coverage") is not None else 0,
            "o_coverage": analysis.get("o_coverage") if analysis.get("o_coverage") is not None else 0,
            "n_enrichment": features.get("n_enrichment"),
            "n_coverage": features.get("n_coverage"),
            "cluster_score": analysis.get("cluster_score") if analysis.get("cluster_score") is not None else 0,
            "result": analysis.get("cv_result"),
            "confidence": analysis.get("cv_confidence"),
        }
    rid = str(row["id"])
    return {
        "id": rid,
        "power": f"{row['power']}W",
        "time": f"{row['time_sec']}s",
        "wafer": row["wafer"],
        "point": row["point"],
        "zone": row.get("position") or "Unknown",
        "page": features.get("page"),
        "human_result": row.get("human_result"),
        "human_verified_at": row.get("human_verified_at"),
        "human_updated_at": row.get("human_updated_at"),
        "ai_result": row.get("ai_result"),
        "ai_confidence": row.get("ai_confidence"),
        "ai_rationale": row.get("ai_rationale"),
        "confidence": features.get("confidence"),
        "residue_score": features.get("residue_score", 0),
        "features": features,
        "assets": assets,
        "r2_assets": assets,
    }

@app.get("/health")
def health():
    return {
        "ok":True,
        "openai_configured":ai_available(),
        "model":os.getenv("OPENAI_MODEL","gpt-5.6-luna"),
        "r2_configured":r2.configured,
        "supabase_configured":db.configured(),
    }

def process_upload_job(job_id, project_id, pdir, pdf_paths, saved, source_hashes, source_reuse, conditions, pages_per_point, substrate_type, sample_category, treatment, repeat_no):
    """Background processor so the browser is not held open for the full PDF analysis."""
    try:
        normalized = normalize_conditions(conditions)
        expected_points = len(condition_point_sequence(normalized))
        expected_pages = expected_points * pages_per_point

        # Persist the original source PDF outside the Render filesystem. This is
        # intentionally done after the HTTP request has returned.
        source_keys = {}
        if r2.configured:
            set_job(job_id, phase="storage", progress=7, message="원본 PDF를 저장하고 있습니다.", total=expected_points, completed=0)
            for path in pdf_paths:
                if path.name in source_reuse:
                    source_keys[path.name] = source_reuse[path.name]
                    continue
                content_type = mimetypes.guess_type(path.name)[0] or "application/pdf"
                key = f"projects/{project_id}/source/{path.name}"
                r2.upload_file(path, key, content_type)
                source_keys[path.name] = key
        else:
            source_keys = {}

        # Validate page count in the background, so a large PDF never blocks the
        # upload HTTP request.
        set_job(job_id, phase="validation", progress=9, message="PDF 페이지 구조를 확인하고 있습니다.", total=expected_points, completed=0)
        import pymupdf as fitz
        total_pages = 0
        for path in pdf_paths:
            with fitz.open(path) as doc:
                total_pages += len(doc)
        if total_pages != expected_pages:
            raise ValueError(
                f"Page count mismatch: expected {expected_pages} pages "
                f"({expected_points} points × {pages_per_point} pages/point), "
                f"but received {total_pages} pages. Check condition order/count."
            )

        set_job(job_id, status="processing", phase="analysis", progress=10, message="PDF 분석을 시작했습니다.", total=expected_points, completed=0)

        def point_progress(done, total, phase):
            # Analysis occupies roughly 10-75% of the visible progress bar.
            pct = 10 + int((done / max(total, 1)) * 65)
            set_job(job_id, phase="analysis", progress=min(75, pct), completed=done, total=total,
                    message=f"Point 분석 중 · {done}/{total}")

        gt_examples=[]
        if db.configured():
            try:
                # Existing verified points from this project are the project-specific
                # Ground Truth reference set. On a first upload this is empty; later
                # re-analyses automatically reuse the accumulated examples.
                gt_examples=db.get_ground_truth_examples(project_id, limit=8)
            except Exception as e:
                print(f"[ground_truth] upload context lookup failed: {e}")
        all_records = extract_pdfs(
            pdf_paths, pdir / "assets", conditions, pages_per_point,
            progress_callback=point_progress, roi_reference_examples=gt_examples,
        )
        if not all_records:
            raise ValueError("No analyzable PDF points were found.")

        set_job(job_id, phase="database", progress=76, completed=0, total=len(all_records), message="분석 결과를 저장하고 있습니다.")
        if db.configured():
            db.create_project(project_id, f"UV Tape Residue · {substrate_type} · Repeat {repeat_no}", json.dumps({"files": saved, "conditions": conditions, "substrate_type": substrate_type, "sample_category": sample_category, "repeat_no": repeat_no, "pages_per_point": pages_per_point, "source_hashes": source_hashes, "source_keys": source_keys}, ensure_ascii=False))

        for i, r in enumerate(all_records, 1):
            source_point_id = r["id"]
            if r2.configured:
                assets = list(r.get("assets", {}).items())
                for asset_type, local_path in assets:
                    lp = Path(local_path)
                    key = f"projects/{project_id}/points/{source_point_id}/{asset_type}{lp.suffix.lower() or '.jpg'}"
                    r2.upload_file(lp, key, "image/jpeg")
                    r.setdefault("r2_assets", {})[asset_type] = key
            if db.configured():
                db_row = db.upsert_point(project_id, r)
                db_point_id = str(db_row["id"])
                r["db_id"] = db_point_id
                r["id"] = db_point_id
                db.upsert_analysis(db_point_id, r.get("features", {}))
                for asset_type, key in r.get("r2_assets", {}).items():
                    db.upsert_asset(db_point_id, asset_type, key)
            RECORDS[r["id"]] = r
            pct = 76 + int((i / max(len(all_records), 1)) * 23)
            set_job(job_id, phase="database", progress=min(99, pct), completed=i, total=len(all_records), message=f"데이터 저장 중 · {i}/{len(all_records)}")

        PROJECTS[project_id] = {
            "id": project_id,
            "condition_count": len(conditions),
            "conditions": conditions,
            "pages_per_point": pages_per_point,
            "files": saved,
            "records": [r["id"] for r in all_records],
        }
        set_job(job_id, status="completed", phase="complete", progress=100, completed=len(all_records), total=len(all_records),
                project_id=project_id, count=len(all_records), message="분석이 완료되었습니다.")
    except Exception as e:
        traceback.print_exc()
        set_job(job_id, status="failed", phase="error", progress=0, error=str(e), message=f"분석 실패: {e}")

@app.post("/api/upload")
async def upload(
    files: list[UploadFile] = File(...),
    conditions_json: str = Form(...),
    pages_per_point: int = Form(3),
    substrate_type: str = Form("SiCN"),
    sample_category: str = Form("MAIN"),
    treatment: str = Form("CMP"),
):
    """Receive source PDFs, validate mapping, then process them in the background."""
    try:
        conditions = json.loads(conditions_json)
        if not isinstance(conditions, list):
            raise ValueError("conditions_json must be a list")
        normalized = normalize_conditions(conditions)
    except Exception as e:
        raise HTTPException(400, f"Invalid condition settings: {e}")
    if pages_per_point < 1:
        raise HTTPException(400, "pages_per_point must be at least 1")

    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    pdir = UPLOAD / project_id
    pdir.mkdir(parents=True, exist_ok=True)
    pdf_paths = []
    saved = []
    source_hashes = {}

    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext != ".pdf":
            continue
        safe_name = Path(f.filename or "upload.pdf").name
        target = pdir / safe_name
        sha = hashlib.sha256()
        with target.open("wb") as out:
            while True:
                chunk = f.file.read(1024*1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
        source_hashes[safe_name] = sha.hexdigest()
        saved.append(safe_name)
        pdf_paths.append(target)

    if not pdf_paths:
        raise HTTPException(400, "PDF 파일을 하나 이상 업로드하세요.")

    # IMPORTANT: do not parse the PDF, upload it to R2, or validate page counts
    # inside the HTTP request. The browser upload can finish while the server is
    # still handling the request body, and any heavy work here can cause Render
    # to keep the connection open until the instance is killed.
    expected_points = len(condition_point_sequence(normalized))
    expected_pages = expected_points * pages_per_point

    repeat_no = 1
    source_reuse = {}
    if db.configured():
        try:
            existing = db.get_client().table("projects").select("id,description").execute().data or []
            signature = json.dumps({"substrate_type": substrate_type, "conditions": normalized}, sort_keys=True, separators=(",", ":"))
            for row in existing:
                try:
                    meta = json.loads(row.get("description") or "{}")
                except Exception:
                    continue
                old_sig = json.dumps({"substrate_type": meta.get("substrate_type", "SiCN"), "conditions": meta.get("conditions", [])}, sort_keys=True, separators=(",", ":"))
                if old_sig == signature:
                    repeat_no = max(repeat_no, int(meta.get("repeat_no", 0) or 0) + 1)
                    old_hashes = meta.get("source_hashes") or {}
                    old_keys = meta.get("source_keys") or {}
                    for name, sha in source_hashes.items():
                        if old_hashes.get(name) == sha and old_keys.get(name):
                            source_reuse[name] = old_keys[name]
        except Exception as e:
            print(f"[repeat] lookup failed: {e}")
    set_job(job_id, status="queued", phase="queued", progress=5, completed=0,
            total=expected_points, project_id=project_id,
            message=f"파일 업로드 완료 · Repeat {repeat_no} · 분석 대기 중")
    threading.Thread(
        target=process_upload_job,
        args=(job_id, project_id, pdir, pdf_paths, saved, source_hashes, source_reuse, conditions, pages_per_point, substrate_type, sample_category, treatment, repeat_no),
        daemon=True,
    ).start()

    return {
        "job_id": job_id,
        "project_id": project_id,
        "condition_count": len(conditions),
        "conditions": conditions,
        "pages_per_point": pages_per_point,
        "files": saved,
        "expected_points": expected_points,
        "expected_pages": expected_pages,
        "repeat_no": repeat_no,
        "substrate_type": substrate_type,
        "sample_category": sample_category,
        "treatment": treatment,
    }

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job

def _r2_client():
    import boto3
    client=boto3.client("s3",endpoint_url=os.getenv("R2_ENDPOINT_URL"),aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),region_name="auto")
    return client,os.getenv("R2_BUCKET_NAME")

def _download_r2_source(key: str, destination: Path):
    client,bucket=_r2_client(); destination.parent.mkdir(parents=True,exist_ok=True); client.download_file(bucket,key,str(destination))

def process_reanalysis_job(job_id,project_id,meta):
    try:
        conditions=meta.get("conditions") or []; pages_per_point=int(meta.get("pages_per_point",3) or 3); files=meta.get("files") or []; keys=meta.get("source_keys") or {}
        if not files: raise ValueError("Stored source PDF metadata was not found.")
        temp=UPLOAD/"_reanalyze"/project_id
        if temp.exists(): shutil.rmtree(temp)
        temp.mkdir(parents=True,exist_ok=True)
        pdf_paths=[]
        for name in files:
            key=keys.get(name) or f"projects/{project_id}/source/{name}"; local=temp/Path(name).name; _download_r2_source(key,local); pdf_paths.append(local)
        expected=len(condition_point_sequence(normalize_conditions(conditions)))
        set_job(job_id,status="processing",phase="analysis",progress=10,completed=0,total=expected,message="R2 원본 PDF로 재분석 중입니다.")
        def point_progress(done,total,phase): set_job(job_id,phase="analysis",progress=min(75,10+int(done/max(total,1)*65)),completed=done,total=total,message=f"재분석 중 · {done}/{total}")
        gt_examples=[]
        if db.configured():
            try:
                gt_examples=db.get_ground_truth_examples(project_id, limit=8)
            except Exception as e:
                print(f"[ground_truth] reanalysis context lookup failed: {e}")
        records=extract_pdfs(pdf_paths,temp/"assets",conditions,pages_per_point,point_progress,roi_reference_examples=gt_examples)
        set_job(job_id,phase="database",progress=76,completed=0,total=len(records),message="재분석 결과를 기존 Point에 반영하고 있습니다.")
        for i,r in enumerate(records,1):
            source_point_id=r["id"]
            if r2.configured:
                for asset_type,local_path in list(r.get("assets",{}).items()):
                    lp=Path(local_path); key=f"projects/{project_id}/points/{source_point_id}/{asset_type}{lp.suffix.lower() or '.jpg'}"; r2.upload_file(lp,key,"image/jpeg"); r.setdefault("r2_assets",{})[asset_type]=key
            if db.configured():
                pm=re.search(r"\d+",r["power"]); tm=re.search(r"\d+",r["time"])
                row=db.get_point_by_key(project_id,int(pm.group()),int(tm.group()),int(r["wafer"]),int(r["point"])) if pm and tm else None
                if not row:
                    # Only a genuinely missing point is inserted. Existing points are
                    # never upserted here, so Human verification/AI metadata cannot be
                    # accidentally reset by a re-analysis.
                    row=db.upsert_point(project_id,r)
                else:
                    r["human_result"]=row.get("human_result")
                    r["human_confidence"]=row.get("human_confidence")
                    r["human_verified_at"]=row.get("human_verified_at")
                    r["human_updated_at"]=row.get("human_updated_at")
                    r["ai_result"]=row.get("ai_result")
                    r["ai_confidence"]=row.get("ai_confidence")
                    r["ai_rationale"]=row.get("ai_rationale")
                r["db_id"]=str(row["id"]); r["id"]=str(row["id"])
                db.upsert_analysis(r["db_id"],r.get("features",{}))
                for asset_type,key in r.get("r2_assets",{}).items(): db.upsert_asset(r["db_id"],asset_type,key)
            RECORDS[r["id"]]=r; set_job(job_id,phase="database",progress=min(99,76+int(i/max(len(records),1)*23)),completed=i,total=len(records),message=f"재분석 결과 저장 중 · {i}/{len(records)}")
        shutil.rmtree(temp,ignore_errors=True); set_job(job_id,status="completed",phase="complete",progress=100,completed=len(records),total=len(records),project_id=project_id,count=len(records),message="기존 데이터 재분석이 완료되었습니다.")
    except Exception as e:
        traceback.print_exc(); set_job(job_id,status="failed",phase="error",progress=0,error=str(e),message=f"재분석 실패: {e}")

@app.post("/api/projects/{project_id}/reanalyze")
def reanalyze_project(project_id: str):
    if not db.configured() or not r2.configured: raise HTTPException(503,"Supabase/R2 is not configured.")
    try: p=db.get_project(project_id)
    except Exception: raise HTTPException(404,"project not found")
    try: meta=json.loads(p.get("description") or "{}")
    except Exception: meta={}
    if not meta.get("files"): raise HTTPException(400,"Stored source PDF metadata was not found for this project.")
    job_id=str(uuid.uuid4()); expected=len(condition_point_sequence(normalize_conditions(meta.get("conditions") or [])))
    set_job(job_id,status="queued",phase="queued",progress=5,completed=0,total=expected,project_id=project_id,message="R2에 저장된 원본 PDF를 불러오는 중입니다.")
    threading.Thread(target=process_reanalysis_job,args=(job_id,project_id,meta),daemon=True).start()
    return {"job_id":job_id,"project_id":project_id,"expected_points":expected}

@app.get("/api/projects/latest")
def latest_project():
    if not db.configured():
        return {"project_id": None, "points": []}
    try:
        p = db.get_latest_project()
        if not p:
            return {"project_id": None, "points": []}
        project_id = p["id"]
        packed = db.get_points_with_data(project_id)
    except Exception as e:
        print(f"[latest_project] Supabase lookup failed: {type(e).__name__}: {e}")
        raise HTTPException(503, "Project data could not be loaded.")
    recs = [db_record(project_id, row, analysis, assets) for row, analysis, assets in packed]
    for r in recs:
        RECORDS[r["id"]] = r
    PROJECTS[project_id] = {
        "id": project_id,
        "condition_count": None,
        "files": [],
        "records": [r["id"] for r in recs],
    }
    return {"project_id": project_id, "points": [public_record(r) for r in recs]}

@app.get("/api/projects/{project_id}")
def project(project_id:str):
    if project_id in PROJECTS:
        p=PROJECTS[project_id]
        return {**p,"points":[public_record(RECORDS[x]) for x in p["records"]]}
    if not db.configured():
        raise HTTPException(404,"project not found")
    try:
        p=db.get_project(project_id)
        packed=db.get_points_with_data(project_id)
    except Exception:
        raise HTTPException(404,"project not found")
    recs=[db_record(project_id,row,analysis,assets) for row,analysis,assets in packed]
    for r in recs:
        RECORDS[r["id"]]=r
    PROJECTS[project_id]={"id":project_id,"condition_count":None,"files":[],"records":[r["id"] for r in recs]}
    return {**PROJECTS[project_id],"points":[public_record(r) for r in recs]}

@app.get("/api/points/{point_id}")
def point(point_id:str):
    if point_id in RECORDS:
        return public_record(RECORDS[point_id])
    if not db.configured():
        raise HTTPException(404,"point not found")
    # Search by point id through Supabase-backed project rows.
    try:
        row=db.get_client().table("points").select("*").eq("id",point_id).single().execute().data
    except Exception:
        raise HTTPException(404,"point not found")
    r=db_record(row["project_id"],row)
    RECORDS[point_id]=r
    return public_record(r)


def _collect_asset_keys(point_id: str) -> dict:
    """Return all known raw/R2 assets for a point, including older projects."""
    keys = {}
    r = RECORDS.get(point_id)
    if r:
        keys.update(r.get("r2_assets") or {})
        # Some legacy in-memory records keep storage keys only in assets.
        for k, v in (r.get("assets") or {}).items():
            if isinstance(v, str) and not v.startswith("/") and not v.startswith("http"):
                keys.setdefault(k, v)
    if db.configured():
        try:
            for a in db.get_assets(point_id):
                keys.setdefault(a["asset_type"], a["storage_path"])
        except Exception:
            pass
    return keys


def _dynamic_asset(point_id: str, asset_type: str):
    """Generate a no-Local-Ring overlay for legacy points that lack v21 assets.

    This is a compatibility path only. New uploads/re-analysis still write the
    normal permanent assets to R2. The generated files are cached on the backend
    so opening Verification does not rerun CV for every request.
    """
    if not r2.configured:
        return None
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(point_id))
    outdir = OUTPUT / "_dynamic_assets" / safe_id
    outdir.mkdir(parents=True, exist_ok=True)
    name_map = {
        "sem_residue_overlay": "sem_dynamic_overlay.jpg",
        "eds_co_overlay": "eds_dynamic_overlay.jpg",
        "c_map_enhanced_overlay": "c_dynamic_overlay.jpg",
        "o_map_enhanced_overlay": "o_dynamic_overlay.jpg",
    }
    filename = name_map.get(asset_type)
    if not filename:
        return None
    target = outdir / filename
    if target.exists() and target.stat().st_size > 500:
        return str(target)

    keys = _collect_asset_keys(point_id)
    # Never use an old overlay as the SEM source; use the original SEM whenever available.
    def load(label, aliases=()):
        key = keys.get(label)
        if not key:
            for a in aliases:
                if keys.get(a):
                    key = keys[a]; break
        if not key:
            return None
        cache = outdir / f"raw_{label}.jpg"
        try:
            if not cache.exists():
                r2.download_file(key, cache)
            im = cv2.imread(str(cache), cv2.IMREAD_COLOR)
            return im if im is not None and im.size else None
        except Exception:
            return None

    sem = load("sem")
    if sem is None:
        # A legacy project may only have the previous overlay. It is still better to
        # show the image than a blank Verification panel.
        sem = load("sem_residue_overlay")
    if sem is None:
        return None

    # Import lazily so the normal FastAPI startup remains lightweight.
    from .point_worker import (
        residue_features, detect_residue_candidates, make_box_overlay,
        make_co_overlay, enhance_element_map, analytical_mask, polygon_to_mask,
    )

    c = load("c_map")
    o = load("o_map")
    n = load("n_map")
    si = load("si_map")

    if c is not None and o is not None and n is not None and si is not None:
        try:
            features, roi = residue_features(sem, c, o, n, si)
            stored_features=(RECORDS.get(point_id) or {}).get("features",{})
            stored_polygons=stored_features.get("human_roi_polygons") or stored_features.get("human_roi_polygon")
            if stored_polygons:
                # Human ROI may contain multiple disconnected polygons.  Use the
                # complete saved mask, not only the legacy first polygon.
                human_mask=polygon_to_mask(sem.shape,stored_polygons)
                if cv2.countNonZero(human_mask)>=20: roi=human_mask
        except Exception:
            features, roi = None, None
    else:
        candidates = detect_residue_candidates(sem, 8)
        roi = candidates[0]["mask"] if candidates else np.zeros(sem.shape[:2], np.uint8)

    if roi is None:
        return None

    if asset_type == "sem_residue_overlay":
        out = make_box_overlay(sem, roi, roi_color=(0, 0, 255))
    elif asset_type == "eds_co_overlay":
        if c is None or o is None:
            out = load("eds_map", ("eds_co_overlay", "element_maps_enhanced"))
            if out is None:
                return None
        else:
            out = make_co_overlay(c, o, roi)
    else:
        src = c if asset_type == "c_map_enhanced_overlay" else o
        label = "C" if asset_type == "c_map_enhanced_overlay" else "O"
        if src is None:
            return None
        rr = cv2.resize(roi, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_NEAREST)
        out = make_box_overlay(enhance_element_map(src, label), rr, roi_color=(255, 255, 255))
    if out is None:
        return None
    cv2.imwrite(str(target), out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return str(target) if target.exists() else None



def _load_raw_point_image(point_id: str, label: str):
    """Load an original SEM/EDS image for manual ROI recalculation."""
    r=RECORDS.get(point_id)
    local=(r.get("assets") or {}).get(label) if r else None
    if local and Path(local).exists():
        im=cv2.imread(str(local),cv2.IMREAD_COLOR)
        if im is not None: return im
    if not r2.configured: return None
    keys=_collect_asset_keys(point_id); key=keys.get(label)
    if not key: return None
    safe_id=re.sub(r"[^A-Za-z0-9_-]","_",str(point_id)); outdir=OUTPUT/"_dynamic_assets"/safe_id; outdir.mkdir(parents=True,exist_ok=True)
    cache=outdir/f"manual_raw_{label}.jpg"
    try:
        if not cache.exists(): r2.download_file(key,cache)
        im=cv2.imread(str(cache),cv2.IMREAD_COLOR)
        return im if im is not None else None
    except Exception: return None


def _human_roi_mask_for_point(point_id: str, shape):
    r=RECORDS.get(point_id) or {}
    f=r.get("features") or {}
    polygons=f.get("human_roi_polygons") or f.get("human_roi_polygon")
    return __import__('backend.app.point_worker',fromlist=['polygon_to_mask']).polygon_to_mask(shape,polygons)


def _invalidate_dynamic_assets(point_id: str):
    safe_id=re.sub(r"[^A-Za-z0-9_-]","_",str(point_id)); outdir=OUTPUT/"_dynamic_assets"/safe_id
    for name in ["sem_dynamic_overlay.jpg","c_dynamic_overlay.jpg","o_dynamic_overlay.jpg","eds_dynamic_overlay.jpg"]:
        try: (outdir/name).unlink(missing_ok=True)
        except Exception: pass


@app.post("/api/points/{point_id}/human-roi")
def human_roi(point_id: str, payload: dict = Body(...)):
    if point_id not in RECORDS:
        point(point_id)
    polygon=payload.get("polygon") if isinstance(payload,dict) else None
    polygons=payload.get("polygons") if isinstance(payload,dict) else None
    if polygons is None and isinstance(polygon,list):
        polygons=[polygon]
    if not isinstance(polygons,list) or not polygons:
        raise HTTPException(400,"ROI must contain at least one polygon.")
    polygons=[poly for poly in polygons if isinstance(poly,list) and len(poly)>=3]
    if not polygons:
        raise HTTPException(400,"Each ROI region must contain at least 3 points.")
    from .point_worker import polygon_to_mask, human_roi_features, ai_boxes_to_mask
    sem=_load_raw_point_image(point_id,"sem")
    c=_load_raw_point_image(point_id,"c_map"); o=_load_raw_point_image(point_id,"o_map")
    n=_load_raw_point_image(point_id,"n_map"); si=_load_raw_point_image(point_id,"si_map")
    if any(x is None for x in [sem,c,o,n,si]):
        raise HTTPException(503,"Original SEM/C/O/N/Si assets are not available for manual ROI analysis.")
    mask=polygon_to_mask(sem.shape,polygons)
    if cv2.countNonZero(mask)<20: raise HTTPException(400,"ROI is too small or outside the analytical image.")
    hf=human_roi_features(sem,c,o,n,si,mask)
    r=RECORDS[point_id]; f=dict(r.get("features") or {})
    f.update(hf)
    clean_polygons=[[{"x":round(float(q.get("x",0)),6),"y":round(float(q.get("y",0)),6)} for q in poly if isinstance(q,dict)] for poly in polygons]
    clean_polygons=[poly for poly in clean_polygons if len(poly)>=3]
    f["human_roi_polygons"]=clean_polygons
    # Backward compatibility for older UI/data consumers: keep the first polygon here.
    f["human_roi_polygon"]=clean_polygons[0] if clean_polygons else []
    f["human_roi_saved_at"] = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    f["human_roi_source"]="manual_verification"
    ai_boxes=f.get("ai_roi_boxes") or []
    if ai_boxes:
        ai_mask=ai_boxes_to_mask(sem.shape,ai_boxes)
        inter=cv2.countNonZero(cv2.bitwise_and(mask,ai_mask)); union=cv2.countNonZero(cv2.bitwise_or(mask,ai_mask))
        f["human_roi_vs_ai_iou"]=round(inter/max(union,1),4)
    r["features"]=f
    _invalidate_dynamic_assets(point_id)
    if db.configured():
        db.upsert_analysis(point_id,f)
        try: db.add_review_history(point_id,r.get("human_result"),r.get("human_result"),"Manual ROI saved; C/O re-evaluated and stored as Ground Truth candidate.")
        except Exception as e: print(f"[human_roi_history] {e}")
    return public_record(r)

@app.get("/api/assets/{point_id}/{asset_type}")
def asset(point_id:str,asset_type:str):
    r=RECORDS.get(point_id)
    key=None
    db_assets=[]
    if r:
        key=(r.get("r2_assets") or {}).get(asset_type)
        local=(r.get("assets") or {}).get(asset_type)
        # Once a Human ROI is saved, Verification must show the Human ROI contour
        # on SEM/C/O rather than falling back to the original AI/CV overlay.
        if asset_type in {"sem_residue_overlay","c_map_enhanced_overlay","o_map_enhanced_overlay","eds_co_overlay"}:
            hf=r.get("features") or {}
            if hf.get("human_roi_polygons") or hf.get("human_roi_polygon"):
                local_dynamic=_dynamic_asset(point_id, asset_type)
                if local_dynamic:
                    return FileResponse(local_dynamic, media_type="image/jpeg", headers={"Cache-Control":"no-store, max-age=0"})
        if local and Path(local).exists():
            return FileResponse(local,media_type="image/jpeg")
    if db.configured():
        db_assets=db.get_assets(point_id)
        if not key:
            for a in db_assets:
                if a["asset_type"]==asset_type:
                    key=a["storage_path"]
                    break
        # For v21 overlay requests, prefer a fresh CV-generated visual when the
        # exact overlay is absent. This fixes legacy points without requiring an
        # immediate Re-analysis just to make Verification visible.
        if not key and asset_type in {"sem_residue_overlay","eds_co_overlay","c_map_enhanced_overlay","o_map_enhanced_overlay"}:
            local_dynamic=_dynamic_asset(point_id, asset_type)
            if local_dynamic:
                return FileResponse(local_dynamic, media_type="image/jpeg", headers={"Cache-Control":"no-store, max-age=0"})
        if not key:
            aliases = {
                "sem_residue_overlay": ["sem"],
                "eds_co_overlay": ["eds_map", "element_maps_enhanced", "full_element_maps_original"],
                "eds_map": ["full_element_maps_original", "element_maps_enhanced"],
                "c_map_enhanced_overlay": ["c_map"],
                "n_map_enhanced_overlay": ["n_map"],
                "o_map_enhanced_overlay": ["o_map"],
                "si_map_enhanced_overlay": ["si_map"],
            }
            for fallback in aliases.get(asset_type, []):
                match=next((a for a in db_assets if a["asset_type"]==fallback),None)
                if match:
                    key=match["storage_path"]
                    break
    if key and r2.configured:
        return RedirectResponse(
            r2.presigned_url(key,expires=900),
            headers={"Cache-Control":"no-store, max-age=0"},
        )

    # Compatibility for old points: regenerate the requested v21 visual from the
    # original SEM/C/O maps instead of leaving a blank image until Re-analysis.
    dynamic_type = asset_type
    local = _dynamic_asset(point_id, dynamic_type)
    if local:
        return FileResponse(local, media_type="image/jpeg", headers={"Cache-Control":"no-store, max-age=0"})
    raise HTTPException(404,"asset not found")

@app.post("/api/points/{point_id}/human")
def human(point_id:str,result:str):
    if point_id not in RECORDS:
        point(point_id)
    if result not in {"Residue","Non-residue","Ambiguous","Review","Skip"}:
        raise HTTPException(400,"invalid result")
    value=None if result=="Skip" else result
    previous=RECORDS[point_id].get("human_result")
    RECORDS[point_id]["human_result"]=value
    if db.configured():
        from datetime import datetime, timezone
        verified_at=None if result=="Skip" else datetime.now(timezone.utc).isoformat()
        db.update_point(point_id,human_result=value,human_verified_at=verified_at,human_updated_at=verified_at)
        if result!="Skip":
            try: db.add_review_history(point_id, previous, value)
            except Exception as e: print(f"[review_history] {e}")
        else:
            # A skip deliberately remains unverified.
            pass
    return public_record(RECORDS[point_id])

@app.post("/api/points/{point_id}/ai")
def ai(point_id:str):
    if point_id not in RECORDS:
        point(point_id)
    from .ai import analyze_with_openai
    result=analyze_with_openai(RECORDS[point_id], r2_storage=r2)
    if result.get("result"):
        RECORDS[point_id]["ai_result"]=result["result"]
        RECORDS[point_id]["ai_rationale"]=result.get("rationale","")
        RECORDS[point_id]["ai_evidence"]=result.get("evidence",[])
        RECORDS[point_id]["ai_caveats"]=result.get("caveats",[])
        RECORDS[point_id]["ai_confidence"]=result.get("confidence")
        if db.configured():
            db.update_point(
                point_id,
                ai_result=result["result"],
                ai_confidence=result.get("confidence"),
                ai_rationale=result.get("rationale",""),
            )
    return result

@app.post("/api/projects/{project_id}/ai-analysis")
def project_ai_analysis(project_id: str):
    if project_id not in PROJECTS:
        project(project_id)
    rec=[RECORDS[x] for x in PROJECTS[project_id]["records"]]
    from .ai import analyze_project_with_openai
    return analyze_project_with_openai(rec)

def hydrate_records_for_export(project_id:str):
    p=PROJECTS[project_id]
    rec=[RECORDS[x] for x in p["records"]]
    temp=OUTPUT/"_export_assets"/project_id
    for r in rec:
        for key in ("sem","spectrum","eds_map","element_maps"):
            local=(r.get("assets") or {}).get(key)
            if local and Path(local).exists():
                continue
            r2key=(r.get("r2_assets") or {}).get(key)
            if not r2key and db.configured():
                for a in db.get_assets(r["id"]):
                    if a["asset_type"]==key:
                        r2key=a["storage_path"]; break
            if r2key and r2.configured:
                dest=temp/r["id"]/f"{key}.jpg"
                r2.download_file(r2key,dest)
                r.setdefault("assets",{})[key]=str(dest)
    return rec

@app.post("/api/projects/{project_id}/export/ppt")
def ppt(project_id:str):
    if project_id not in PROJECTS:
        project(project_id)
    from .reports import export_ppt
    rec=hydrate_records_for_export(project_id)
    out=OUTPUT/f"{project_id}_point_report.pptx"; export_ppt(rec,out)
    if r2.configured:
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return FileResponse(out,filename=out.name,media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

@app.post("/api/projects/{project_id}/export/pdf")
def pdf(project_id:str):
    if project_id not in PROJECTS:
        project(project_id)
    from .reports import export_pdf
    rec=hydrate_records_for_export(project_id)
    out=OUTPUT/f"{project_id}_point_report.pdf"; export_pdf(rec,out)
    if r2.configured:
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/pdf")
    return FileResponse(out,filename=out.name,media_type="application/pdf")

@app.post("/api/projects/{project_id}/export/json")
def json_export(project_id:str):
    if project_id not in PROJECTS:
        project(project_id)
    rec=[RECORDS[x] for x in PROJECTS[project_id]["records"]]
    out=OUTPUT/f"{project_id}_analysis.json"
    out.write_text(json.dumps([public_record(r) for r in rec],ensure_ascii=False,indent=2),encoding="utf-8")
    if r2.configured:
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/json")
    return FileResponse(out,filename=out.name,media_type="application/json")
