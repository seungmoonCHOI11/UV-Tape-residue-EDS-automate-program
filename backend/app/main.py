import os, json, uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from .analysis import extract_pdf
from .ai import analyze_with_openai, ai_available
from .reports import export_ppt, export_pdf

load_dotenv()
BASE=Path(__file__).resolve().parents[1]
UPLOAD=Path(os.getenv("UPLOAD_DIR",BASE/"data/uploads"))
OUTPUT=Path(os.getenv("OUTPUT_DIR",BASE/"data/outputs"))
UPLOAD.mkdir(parents=True,exist_ok=True); OUTPUT.mkdir(parents=True,exist_ok=True)

app=FastAPI(title="UV Tape Residue EDS API",version="3.0.0")
origins=[x.strip() for x in os.getenv("CORS_ORIGINS","http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

PROJECTS={}
RECORDS={}

@app.get("/health")
def health():
    return {"ok":True,"openai_configured":ai_available(),"model":os.getenv("OPENAI_MODEL","gpt-5.5")}

@app.post("/api/upload")
async def upload(files:list[UploadFile]=File(...),condition_count:int=7):
    project_id=str(uuid.uuid4())[:8]
    pdir=UPLOAD/project_id; pdir.mkdir(parents=True,exist_ok=True)
    all_records=[]
    saved=[]
    for f in files:
        ext=Path(f.filename or "").suffix.lower()
        if ext not in {".pdf",".csv",".xlsx",".xls",".txt"}:
            continue
        target=pdir/(Path(f.filename).name)
        target.write_bytes(await f.read())
        saved.append(target.name)
        if ext==".pdf":
            try:
                all_records.extend(extract_pdf(target,pdir/"assets"))
            except Exception as e:
                raise HTTPException(400,f"PDF parsing failed: {e}")
    for r in all_records:
        RECORDS[r["id"]]=r
    PROJECTS[project_id]={"id":project_id,"condition_count":condition_count,"files":saved,"records":[r["id"] for r in all_records]}
    return {"project_id":project_id,"condition_count":condition_count,"files":saved,"points":all_records,"count":len(all_records)}

@app.get("/api/projects/{project_id}")
def project(project_id:str):
    if project_id not in PROJECTS: raise HTTPException(404,"project not found")
    p=PROJECTS[project_id]
    return {**p,"points":[RECORDS[x] for x in p["records"]]}

@app.get("/api/points/{point_id}")
def point(point_id:str):
    if point_id not in RECORDS: raise HTTPException(404,"point not found")
    return RECORDS[point_id]

@app.post("/api/points/{point_id}/human")
def human(point_id:str,result:str):
    if point_id not in RECORDS: raise HTTPException(404,"point not found")
    if result not in {"Residue","Non-residue","Review","Skip"}: raise HTTPException(400,"invalid result")
    RECORDS[point_id]["human_result"]=None if result=="Skip" else result
    return RECORDS[point_id]

@app.post("/api/points/{point_id}/ai")
def ai(point_id:str):
    if point_id not in RECORDS: raise HTTPException(404,"point not found")
    result=analyze_with_openai(RECORDS[point_id])
    if result.get("result"):
        RECORDS[point_id]["ai_result"]=result["result"]
        RECORDS[point_id]["ai_rationale"]=result.get("rationale","")
        RECORDS[point_id]["ai_evidence"]=result.get("evidence",[])
        RECORDS[point_id]["ai_caveats"]=result.get("caveats",[])
        RECORDS[point_id]["ai_confidence"]=result.get("confidence")
    return result

@app.post("/api/projects/{project_id}/export/ppt")
def ppt(project_id:str):
    if project_id not in PROJECTS: raise HTTPException(404,"project not found")
    rec=[RECORDS[x] for x in PROJECTS[project_id]["records"]]
    out=OUTPUT/f"{project_id}_point_report.pptx"; export_ppt(rec,out)
    return FileResponse(out,filename=out.name,media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

@app.post("/api/projects/{project_id}/export/pdf")
def pdf(project_id:str):
    if project_id not in PROJECTS: raise HTTPException(404,"project not found")
    rec=[RECORDS[x] for x in PROJECTS[project_id]["records"]]
    out=OUTPUT/f"{project_id}_point_report.pdf"; export_pdf(rec,out)
    return FileResponse(out,filename=out.name,media_type="application/pdf")

@app.post("/api/projects/{project_id}/export/json")
def json_export(project_id:str):
    if project_id not in PROJECTS: raise HTTPException(404,"project not found")
    out=OUTPUT/f"{project_id}_analysis.json"
    out.write_text(json.dumps([RECORDS[x] for x in PROJECTS[project_id]["records"]],ensure_ascii=False,indent=2),encoding="utf-8")
    return FileResponse(out,filename=out.name,media_type="application/json")
