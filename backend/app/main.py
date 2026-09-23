
import os, json, uuid, shutil, mimetypes, threading, traceback
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
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

app=FastAPI(title="UV Tape Residue EDS API",version="9.0.0")
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
    out["assets"]={k:f"/api/assets/{quote(r['id'],safe='')}/{quote(k,safe='')}" for k in r.get("assets",{})}
    out.pop("r2_assets", None)
    return out

def db_record(project_id: str, row: dict) -> dict:
    analysis=db.get_analysis(row["id"]) if db.configured() else None
    assets=db.get_assets(row["id"]) if db.configured() else []
    asset_map={a["asset_type"]:a["storage_path"] for a in assets}
    features=(analysis or {}).get("features") or {}
    if analysis:
        features={**features,
            "residue_score":analysis.get("residue_score") or 0,
            "c_enrichment":analysis.get("c_enrichment") or 0,
            "o_enrichment":analysis.get("o_enrichment") or 0,
            "c_coverage":analysis.get("c_coverage") or 0,
            "o_coverage":analysis.get("o_coverage") or 0,
            "cluster_score":analysis.get("cluster_score") or 0,
            "result":analysis.get("cv_result"),
            "confidence":analysis.get("cv_confidence"),
        }
    rid=str(row["id"])
    return {
        "id":rid,
        "power":f"{row['power']}W",
        "time":f"{row['time_sec']}s",
        "wafer":row["wafer"],
        "point":row["point"],
        "zone":row.get("position") or "Unknown",
        "page":None,
        "human_result":row.get("human_result"),
        "ai_result":row.get("ai_result"),
        "ai_confidence":row.get("ai_confidence"),
        "ai_rationale":row.get("ai_rationale"),
        "confidence":features.get("confidence"),
        "residue_score":features.get("residue_score",0),
        "features":features,
        "assets":asset_map,
        "r2_assets":asset_map,
    }

@app.get("/health")
def health():
    return {
        "ok":True,
        "openai_configured":ai_available(),
        "model":os.getenv("OPENAI_MODEL","gpt-5.5"),
        "r2_configured":r2.configured,
        "supabase_configured":db.configured(),
    }

def process_upload_job(job_id, project_id, pdir, pdf_paths, saved, conditions, pages_per_point):
    """Background processor so the browser is not held open for the full PDF analysis."""
    try:
        normalized = normalize_conditions(conditions)
        expected_points = len(condition_point_sequence(normalized))
        expected_pages = expected_points * pages_per_point

        # Persist the original source PDF outside the Render filesystem. This is
        # intentionally done after the HTTP request has returned.
        if r2.configured:
            set_job(job_id, phase="storage", progress=7, message="원본 PDF를 저장하고 있습니다.", total=expected_points, completed=0)
            for path in pdf_paths:
                content_type = mimetypes.guess_type(path.name)[0] or "application/pdf"
                r2.upload_file(path, f"projects/{project_id}/source/{path.name}", content_type)

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

        all_records = extract_pdfs(
            pdf_paths, pdir / "assets", conditions, pages_per_point,
            progress_callback=point_progress,
        )
        if not all_records:
            raise ValueError("No analyzable PDF points were found.")

        set_job(job_id, phase="database", progress=76, completed=0, total=len(all_records), message="분석 결과를 저장하고 있습니다.")
        if db.configured():
            db.create_project(project_id, "UV Tape Residue", f"Uploaded files: {', '.join(saved)}")

        for i, r in enumerate(all_records, 1):
            RECORDS[r["id"]] = r
            if r2.configured:
                assets = list(r.get("assets", {}).items())
                for asset_type, local_path in assets:
                    lp = Path(local_path)
                    key = f"projects/{project_id}/points/{r['id']}/{asset_type}{lp.suffix.lower() or '.jpg'}"
                    r2.upload_file(lp, key, "image/jpeg")
                    r.setdefault("r2_assets", {})[asset_type] = key
            if db.configured():
                db.upsert_point(project_id, r)
                db.upsert_analysis(r["id"], r.get("features", {}))
                for asset_type, key in r.get("r2_assets", {}).items():
                    db.upsert_asset(r["id"], asset_type, key)
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

    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext != ".pdf":
            continue
        safe_name = Path(f.filename or "upload.pdf").name
        target = pdir / safe_name
        with target.open("wb") as out:
            shutil.copyfileobj(f.file, out, length=1024*1024)
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
    set_job(job_id, status="queued", phase="queued", progress=5, completed=0,
            total=expected_points, project_id=project_id,
            message="파일 업로드 완료 · 분석 대기 중")
    threading.Thread(
        target=process_upload_job,
        args=(job_id, project_id, pdir, pdf_paths, saved, conditions, pages_per_point),
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
    }

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job

@app.get("/api/projects/latest")
def latest_project():
    if not db.configured():
        return {"project_id": None, "points": []}
    try:
        p = db.get_latest_project()
        if not p:
            return {"project_id": None, "points": []}
        project_id = p["id"]
        rows = db.get_points(project_id)
    except Exception as e:
        print(f"[latest_project] Supabase lookup failed: {type(e).__name__}: {e}")
        # Keep the dashboard usable when the database has no readable project yet.
        # The exact Supabase error remains visible in Render logs for diagnosis.
        return {"project_id": None, "points": [], "warning": str(e)}
    recs = [db_record(project_id, row) for row in rows]
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
        rows=db.get_points(project_id)
    except Exception:
        raise HTTPException(404,"project not found")
    recs=[db_record(project_id,row) for row in rows]
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

@app.get("/api/assets/{point_id}/{asset_type}")
def asset(point_id:str,asset_type:str):
    r=RECORDS.get(point_id)
    key=None
    if r:
        key=(r.get("r2_assets") or {}).get(asset_type)
        local=(r.get("assets") or {}).get(asset_type)
        if local and Path(local).exists():
            return FileResponse(local,media_type="image/jpeg")
    if not key and db.configured():
        assets=db.get_assets(point_id)
        for a in assets:
            if a["asset_type"]==asset_type:
                key=a["storage_path"]; break
    if key and r2.configured():
        return RedirectResponse(r2.presigned_url(key,expires=900))
    raise HTTPException(404,"asset not found")

@app.post("/api/points/{point_id}/human")
def human(point_id:str,result:str):
    if point_id not in RECORDS:
        point(point_id)
    if result not in {"Residue","Non-residue","Review","Skip"}:
        raise HTTPException(400,"invalid result")
    value=None if result=="Skip" else result
    RECORDS[point_id]["human_result"]=value
    if db.configured():
        db.update_point(point_id,human_result=value)
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
            if r2key and r2.configured():
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
    if r2.configured():
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return FileResponse(out,filename=out.name,media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

@app.post("/api/projects/{project_id}/export/pdf")
def pdf(project_id:str):
    if project_id not in PROJECTS:
        project(project_id)
    from .reports import export_pdf
    rec=hydrate_records_for_export(project_id)
    out=OUTPUT/f"{project_id}_point_report.pdf"; export_pdf(rec,out)
    if r2.configured():
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/pdf")
    return FileResponse(out,filename=out.name,media_type="application/pdf")

@app.post("/api/projects/{project_id}/export/json")
def json_export(project_id:str):
    if project_id not in PROJECTS:
        project(project_id)
    rec=[RECORDS[x] for x in PROJECTS[project_id]["records"]]
    out=OUTPUT/f"{project_id}_analysis.json"
    out.write_text(json.dumps([public_record(r) for r in rec],ensure_ascii=False,indent=2),encoding="utf-8")
    if r2.configured():
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/json")
    return FileResponse(out,filename=out.name,media_type="application/json")
