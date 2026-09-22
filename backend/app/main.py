
import os, json, uuid, shutil, mimetypes
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from dotenv import load_dotenv

from .analysis import extract_pdf
from .ai import analyze_with_openai, ai_available
from .reports import export_ppt, export_pdf
from .r2_storage import r2
from . import db

load_dotenv()
BASE=Path(__file__).resolve().parents[1]
UPLOAD=Path(os.getenv("UPLOAD_DIR",BASE/"data/uploads"))
OUTPUT=Path(os.getenv("OUTPUT_DIR",BASE/"data/outputs"))
UPLOAD.mkdir(parents=True,exist_ok=True); OUTPUT.mkdir(parents=True,exist_ok=True)

app=FastAPI(title="UV Tape Residue EDS API",version="4.0.0")
origins=[x.strip() for x in os.getenv("CORS_ORIGINS","http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

PROJECTS={}
RECORDS={}

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

@app.post("/api/upload")
async def upload(files:list[UploadFile]=File(...),condition_count:int=7):
    project_id=str(uuid.uuid4())
    pdir=UPLOAD/project_id
    pdir.mkdir(parents=True,exist_ok=True)
    all_records=[]
    saved=[]
    for f in files:
        ext=Path(f.filename or "").suffix.lower()
        if ext not in {".pdf",".csv",".xlsx",".xls",".txt"}:
            continue
        safe_name=Path(f.filename or "upload").name
        target=pdir/safe_name
        with target.open("wb") as out:
            shutil.copyfileobj(f.file,out,length=1024*1024)
        saved.append(safe_name)

        if r2.configured:
            content_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
            r2.upload_file(target,f"projects/{project_id}/source/{safe_name}",content_type)

        if ext==".pdf":
            try:
                all_records.extend(extract_pdf(target,pdir/"assets"))
            except Exception as e:
                raise HTTPException(400,f"PDF parsing failed: {e}")

    if not all_records:
        raise HTTPException(400,"No analyzable PDF points were found.")

    # Persist project and all point-level data.
    if db.configured():
        try:
            db.create_project(project_id, "UV Tape Residue", f"Uploaded files: {', '.join(saved)}")
        except Exception as e:
            raise HTTPException(500,f"Supabase project save failed: {e}")

    for r in all_records:
        RECORDS[r["id"]]=r
        if r2.configured():
            for asset_type, local_path in r.get("assets",{}).items():
                lp=Path(local_path)
                key=f"projects/{project_id}/points/{r['id']}/{asset_type}{lp.suffix.lower() or '.jpg'}"
                r2.upload_file(lp,key,"image/jpeg")
                r.setdefault("r2_assets",{})[asset_type]=key
        if db.configured():
            try:
                db.upsert_point(project_id,r)
                db.upsert_analysis(r["id"],r.get("features",{}))
                for asset_type,key in r.get("r2_assets",{}).items():
                    db.upsert_asset(r["id"],asset_type,key)
            except Exception as e:
                raise HTTPException(500,f"Supabase point save failed: {e}")

    PROJECTS[project_id]={
        "id":project_id,"condition_count":condition_count,"files":saved,
        "records":[r["id"] for r in all_records]
    }
    return {
        "project_id":project_id,
        "condition_count":condition_count,
        "files":saved,
        "points":[public_record(r) for r in all_records],
        "count":len(all_records),
    }

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
    rec=hydrate_records_for_export(project_id)
    out=OUTPUT/f"{project_id}_point_report.pptx"; export_ppt(rec,out)
    if r2.configured():
        r2.upload_file(out,f"projects/{project_id}/reports/{out.name}","application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return FileResponse(out,filename=out.name,media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

@app.post("/api/projects/{project_id}/export/pdf")
def pdf(project_id:str):
    if project_id not in PROJECTS:
        project(project_id)
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
