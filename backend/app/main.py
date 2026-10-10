
import os, json, uuid, shutil, mimetypes, threading, traceback, hashlib, re, queue, time
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
from .persistence import persist_point, reconcile_project, build_manifest, PersistenceError
from .classification import C_RESIDUE_RATIO, O_RESIDUE_RATIO, MODERATE_RATIO, co_ratio_score, co_rule_result, CLASSIFICATION_RULE_TEXT
from . import db

load_dotenv()
BASE=Path(__file__).resolve().parents[1]
UPLOAD=Path(os.getenv("UPLOAD_DIR",BASE/"data/uploads"))
OUTPUT=Path(os.getenv("OUTPUT_DIR",BASE/"data/outputs"))
UPLOAD.mkdir(parents=True,exist_ok=True); OUTPUT.mkdir(parents=True,exist_ok=True)

app=FastAPI(title="UV Tape Residue EDS API",version="23.7.50")
# Browser frontend is hosted on Vercel while this API is hosted separately.
# The API does not use browser credentials/cookies, so allow cross-origin requests
# from Vercel and other configured origins. This prevents XHR from surfacing a
# misleading "server connection failed" when the response is otherwise healthy.
origins=[x.strip() for x in os.getenv("CORS_ORIGINS","http://localhost:3000").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_origin_regex=r"https://([a-zA-Z0-9-]+\.)*vercel\.app$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECTS={}
RECORDS={}
JOBS={}
JOB_LOCK=threading.Lock()
UPLOAD_QUEUE=queue.Queue()
ANALYSIS_SLOT=threading.Lock()
JOB_PERSIST_LOCK=threading.Lock()
CANCELLED_PROJECTS=set()

def set_job(job_id, **updates):
    with JOB_LOCK:
        JOBS.setdefault(job_id, {}).update(updates)
        snapshot=dict(JOBS[job_id])
    # New uploads use existing project metadata: no schema migration is required.
    if snapshot.get("persist_upload") and db.configured():
        try:
            with JOB_PERSIST_LOCK:
                project=db.get_project(snapshot["project_id"])
                meta=json.loads(project.get("description") or "{}")
                meta["upload_job"]={**snapshot,"job_id":job_id}
                meta["queue_status"]=snapshot.get("status")
                db.update_project(snapshot["project_id"],description=json.dumps(meta,ensure_ascii=False))
        except Exception as exc:
            print(f"[job] progress persistence failed: {exc}")
            with JOB_LOCK:
                JOBS[job_id]["storage_warning"]="진행 상태 저장에 실패했습니다. 현재 서버에서는 분석을 계속하지만 재접속 시 상태가 오래된 값일 수 있습니다."


def get_job(job_id):
    with JOB_LOCK:
        return dict(JOBS.get(job_id, {}))


def normalize_position_substrates(value):
    """Normalize per-position substrate settings. Unknown/missing positions default to SiCN."""
    allowed={"SiCN","Si","SiN","SiO2"}
    try:
        raw=json.loads(value) if isinstance(value,str) else (value or {})
    except Exception as e:
        raise ValueError(f"position_substrates_json must be valid JSON: {e}")
    if not isinstance(raw,dict):
        raise ValueError("position_substrates_json must be an object")
    out={str(i):"SiCN" for i in range(1,10)}
    for k,v in raw.items():
        try: pos=int(k)
        except Exception: continue
        if pos<1 or pos>9: continue
        vv=str(v or "SiCN")
        if vv not in allowed:
            raise ValueError(f"Unsupported substrate for P{pos}: {vv}")
        out[str(pos)]=vv
    return out


def public_record(r: dict) -> dict:
    out=dict(r)
    features=dict(out.get("features") or {})
    # v23.7.46: element-specific residue gates. Saved records are re-evaluated
    # at read time so existing Human ROI and automatic results immediately use
    # C >= 2.40x and O >= 3.00x without requiring a full re-analysis.
    hcr=features.get("human_c_ratio")
    hor=features.get("human_o_ratio")
    has_human=bool(features.get("human_roi_polygons") or features.get("human_roi_polygon"))
    if has_human and isinstance(hcr,(int,float)) and isinstance(hor,(int,float)):
        hscore=round(co_ratio_score(float(hcr),float(hor)),1)
        hresult=co_rule_result(float(hcr),float(hor))
        features["human_residue_score"]=hscore
        features["human_roi_rule_result"]=hresult
        features["human_score_rule"]=CLASSIFICATION_RULE_TEXT
        features["residue_score"]=hscore/100.0
        features["result"]=hresult
        features["confidence"]="Human ROI"
    elif not has_human:
        acr=features.get("c_roi_global_ratio")
        aor=features.get("o_roi_global_ratio")
        if not isinstance(acr,(int,float)):
            crm, cgm=features.get("c_roi_mean"),features.get("c_global_mean")
            if isinstance(crm,(int,float)) and isinstance(cgm,(int,float)) and cgm:
                acr=float(crm)/float(cgm)
        if not isinstance(aor,(int,float)):
            orm, ogm=features.get("o_roi_mean"),features.get("o_global_mean")
            if isinstance(orm,(int,float)) and isinstance(ogm,(int,float)) and ogm:
                aor=float(orm)/float(ogm)
        if isinstance(acr,(int,float)) and isinstance(aor,(int,float)):
            ascore=round(co_ratio_score(float(acr),float(aor)),1)
            aresult=co_rule_result(float(acr),float(aor))
            features["residue_score"]=ascore/100.0
            features["result"]=aresult
            features["classification_note"]="v23.7.50 current rule: "+CLASSIFICATION_RULE_TEXT
            features["c_residue_ratio_threshold"]=C_RESIDUE_RATIO
            features["o_residue_ratio_threshold"]=O_RESIDUE_RATIO
    # Keep score/result/confidence both at the top level and inside features so
    # freshly analyzed in-memory records and Supabase-loaded records render identically.
    out["features"]=features
    if has_human and isinstance(features.get("human_residue_score"),(int,float)):
        out["residue_score"]=features.get("human_residue_score")
        out["confidence"]="Human ROI"
        out["cv_result"]=features.get("human_roi_rule_result")
    else:
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
        "substrate_type": features.get("substrate_type") or "SiCN",
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


def process_upload_job(job_id, project_id, pdir, pdf_paths, saved, source_hashes, source_reuse, conditions, pages_per_point, substrate_type, position_substrates, sample_category, treatment, repeat_no):
    """Background PDF processor with manifest-locked persistence.

    v23.7.47 rules:
      * PDF-to-Point mapping is fixed before analysis. A failed Point never shifts later Points.
      * Every Point save is idempotently retried and read-after-write verified.
      * After the first pass the actual Supabase + R2 dataset is reconciled against the manifest.
      * Missing/incomplete Points are repaired from cached generated assets, then selectively re-run.
      * COMPLETED means the final stored dataset is complete, not merely that 27 workers ran.
    """
    try:
        normalized = normalize_conditions(conditions)
        sequence = condition_point_sequence(normalized, position_substrates)
        manifest = build_manifest(sequence)
        expected_points = len(manifest)
        manifest_by_id = {m["id"]: m for m in manifest}

        # Persist the original source PDF outside Render's ephemeral filesystem.
        source_keys = {}
        storage_warnings = []
        if r2.configured:
            set_job(job_id, phase="storage", progress=7, message="원본 PDF를 저장하고 있습니다.", total=expected_points, completed=0)
            for path in pdf_paths:
                if path.name in source_reuse:
                    source_keys[path.name] = source_reuse[path.name]
                    continue
                content_type = mimetypes.guess_type(path.name)[0] or "application/pdf"
                key = f"projects/{project_id}/source/{path.name}"
                try:
                    r2.upload_file(path, key, content_type)
                    source_keys[path.name] = key
                except Exception as e:
                    storage_warnings.append(f"{path.name}: {e}")
                    print(f"[r2] source upload failed; continuing analysis: {path.name}: {e}")
        if db.configured():
            try:
                meta=json.loads((db.get_project(project_id) or {}).get("description") or "{}")
                meta["source_keys"]=source_keys
                meta["queue_status"]="processing"
                meta["point_manifest"]=[{k:v for k,v in m.items() if k in {"id","sequence_index","power","time","wafer","point","zone","substrate_type"}} for m in manifest]
                db.update_project(project_id, description=json.dumps(meta,ensure_ascii=False), status="Processing")
            except Exception as e:
                print(f"[project] queue metadata update failed: {e}")

        set_job(
            job_id, phase="validation", progress=9,
            message="PDF Point 구조와 고정 매핑을 확인하고 있습니다.",
            total=expected_points, completed=0, failed_count=0, failed_points=[],
            recovered_count=0, recovered_points=[], incomplete_points=[]
        )

        gt_examples=[]
        if db.configured():
            try:
                gt_examples=db.get_ground_truth_examples(project_id, limit=8)
            except Exception as e:
                print(f"[ground_truth] upload context lookup failed: {e}")

        saved_by_source={}
        failure_map={}
        recovered_ids=set()
        record_cache={}
        expected_assets_by_id={}
        structure_info={
            "expected_points": expected_points,
            "detected_points": None,
            "missing_count": None,
            "missing_points": [],
            "recognition_mode": None,
            "mapping_safe": True,
        }

        def _completed_count():
            return len(saved_by_source)

        def _unresolved_failures():
            return [failure_map[k] for k in sorted(failure_map, key=lambda x: int(manifest_by_id.get(x,{}).get("sequence_index") or 10**9))]

        def report_structure(info):
            structure_info.update(info or {})
            detected=int(structure_info.get("detected_points") or 0)
            missing=int(structure_info.get("missing_count") or 0)
            mode=structure_info.get("recognition_mode")
            mode_text="Point label" if mode=="point_label" else "3-page order"
            safe=structure_info.get("mapping_safe",True)
            if not safe:
                message=f"PDF Point 고정 매핑 중단 · {detected}/{expected_points} groups · 순번 밀림 방지를 위해 분석하지 않습니다."
            elif detected == expected_points:
                message=f"PDF Point 고정 매핑 완료 · {detected}/{expected_points} · W/P 슬롯을 잠그고 분석을 시작합니다."
            else:
                message=f"PDF Point 구성 확인 · {detected}/{expected_points} 인식 · 누락 {missing} · 인식된 슬롯만 분석합니다."
            set_job(
                job_id, status="processing", phase="analysis", progress=10,
                message=message, total=expected_points, completed=_completed_count(),
                detected_points=detected, missing_count=missing,
                missing_points=structure_info.get("missing_points") or [],
                recognition_mode=mode, recognition_mode_text=mode_text,
                mapping_safe=safe, manifest_locked=True,
                failed_count=len(failure_map), failed_points=_unresolved_failures(),
            )

        def report_point_failure(failure):
            fid=str(failure.get("id") or "")
            if fid:
                failure_map[fid]=failure
            processed=min(expected_points,_completed_count()+len(failure_map))
            detected=int(structure_info.get("detected_points") or expected_points or 1)
            set_job(
                job_id, phase="analysis",
                completed=_completed_count(), total=expected_points,
                failed_count=len(failure_map), failed_points=_unresolved_failures(),
                processed=processed,
                progress=min(94,10+int(processed/max(detected,1)*82)),
                message=(
                    f"{failure.get('id')} · {failure.get('stage','analysis')} 오류 · 슬롯 유지 후 다음 Point 계속 · "
                    f"저장 {_completed_count()}/{expected_points}"
                ),
            )

        def persistence_status(ev):
            if ev.get("status") != "retry":
                return
            set_job(
                job_id, phase="save_retry",
                completed=_completed_count(), total=expected_points,
                failed_count=len(failure_map), failed_points=_unresolved_failures(),
                message=(
                    f"{ev.get('point_id')} 저장 재시도 {ev.get('attempt')}/{ev.get('max_attempts')} · "
                    f"{ev.get('stage')} · {ev.get('retry_in')}s 후 재시도"
                ),
            )

        def save_point(r):
            if project_id in CANCELLED_PROJECTS:
                raise RuntimeError("분석 중 프로젝트가 삭제되어 작업을 취소했습니다.")
            source_id=str(r.get("source_point_id") or r.get("manifest_id") or r.get("id"))
            r["source_point_id"]=source_id
            record_cache[source_id]=r
            expected_assets_by_id[source_id]=set((r.get("assets") or {}).keys())
            try:
                result=persist_point(
                    project_id,r,db,r2,attempts=3,backoff=(1,2,4),verify=True,
                    status_callback=persistence_status,
                )
            except PersistenceError:
                # Keep the generated images in record_cache. Reconciliation will retry
                # this exact Point without allowing the following Point to take its slot.
                raise

            db_id=str(result["db_id"])
            saved_by_source[source_id]=db_id
            if source_id in failure_map:
                failure_map.pop(source_id,None)
                recovered_ids.add(source_id)
            RECORDS[db_id]=r
            PROJECTS.setdefault(project_id,{})["records"]=list(saved_by_source.values())
            processed=min(expected_points,_completed_count()+len(failure_map))
            detected=int(structure_info.get("detected_points") or expected_points or 1)
            set_job(
                job_id, phase="analysis", completed=_completed_count(), total=expected_points,
                failed_count=len(failure_map), failed_points=_unresolved_failures(),
                recovered_count=len(recovered_ids), recovered_points=sorted(recovered_ids),
                processed=processed,
                progress=min(94,10+int(processed/max(detected,1)*82)),
                message=f"Point 저장 검증 완료 · {_completed_count()}/{expected_points}"
            )

        PROJECTS[project_id]={"id":project_id,"records":[],"files":saved,"conditions":conditions,"manifest":manifest}

        # First pass: analyze every mapped Point. Worker/save failures are isolated.
        extract_pdfs(
            pdf_paths,pdir/"assets",conditions,pages_per_point,
            roi_reference_examples=gt_examples,
            position_substrates=position_substrates,
            record_callback=save_point,
            collect_records=False,
            point_error_callback=report_point_failure,
            structure_callback=report_structure,
            cancel_callback=lambda: project_id in CANCELLED_PROJECTS,
        )

        def run_reconciliation(label):
            set_job(job_id,phase="reconcile",progress=95,completed=_completed_count(),total=expected_points,message=label)
            last_exc=None
            rec=None
            for reconcile_attempt,delay in enumerate((1,2,4),1):
                try:
                    rec=reconcile_project(
                        project_id,manifest,db,r2,
                        expected_assets_by_id=expected_assets_by_id,
                        essential_assets={"sem","eds_map","c_map","o_map"},
                    )
                    break
                except Exception as exc:
                    last_exc=exc
                    if reconcile_attempt>=3:
                        raise
                    set_job(job_id,phase="reconcile",message=f"최종 저장 확인 통신 재시도 {reconcile_attempt}/3 · {type(exc).__name__}: {exc}")
                    time.sleep(delay)
            if rec is None:
                raise RuntimeError(f"Reconciliation failed: {last_exc}")
            # The database/R2 truth, not callback counts, defines completion.
            complete_set=set(rec.get("complete_ids") or [])
            for mid in complete_set:
                if mid in failure_map:
                    failure_map.pop(mid,None)
                    recovered_ids.add(mid)
            for item in rec.get("complete") or []:
                if item.get("db_id"):
                    saved_by_source[item["id"]]=str(item["db_id"])
            set_job(
                job_id,phase="reconcile",completed=rec["complete_count"],total=expected_points,
                failed_count=len(failure_map),failed_points=_unresolved_failures(),
                recovered_count=len(recovered_ids),recovered_points=sorted(recovered_ids),
                incomplete_points=rec.get("incomplete") or [],
                message=f"저장 데이터 전수 확인 · {rec['complete_count']}/{expected_points} complete"
            )
            return rec

        reconciliation=run_reconciliation("1차 분석 결과를 Supabase/R2와 대조하고 있습니다.")

        # Repair 1: if a worker already produced images, reuse those exact images.
        cached_targets=[mid for mid in reconciliation.get("incomplete_ids",[]) if mid in record_cache]
        if cached_targets:
            set_job(job_id,phase="repair",progress=96,message=f"누락/부분저장 {len(cached_targets)} Point를 생성된 이미지로 복구합니다.")
            for mid in cached_targets:
                try:
                    save_point(record_cache[mid])
                    recovered_ids.add(mid)
                except Exception as exc:
                    meta=manifest_by_id.get(mid,{})
                    failure_map[mid]={
                        "id":mid,"power":f"{meta.get('power',0)}W","time":f"{meta.get('time',0)}s",
                        "wafer":meta.get("wafer"),"point":meta.get("point"),"sequence_index":meta.get("sequence_index"),
                        "stage":getattr(exc,"stage","repair_save"),"error":f"{type(exc).__name__}: {exc}",
                    }
            reconciliation=run_reconciliation("저장 재시도 결과를 다시 확인하고 있습니다.")

        # Repair 2: Points without a usable cached worker result are selectively re-run.
        retry_ids=[mid for mid in reconciliation.get("incomplete_ids",[]) if mid not in record_cache]
        retry_indexes={int(manifest_by_id[mid]["sequence_index"])-1 for mid in retry_ids if mid in manifest_by_id}
        if retry_indexes:
            set_job(job_id,phase="repair",progress=97,message=f"미완성 {len(retry_indexes)} Point만 선택 재분석합니다.")
            try:
                extract_pdfs(
                    pdf_paths,pdir/"assets",conditions,pages_per_point,
                    roi_reference_examples=gt_examples,
                    position_substrates=position_substrates,
                    record_callback=save_point,collect_records=False,
                    point_error_callback=report_point_failure,
                    cancel_callback=lambda: project_id in CANCELLED_PROJECTS,
                    target_sequence_indexes=retry_indexes,
                )
            except Exception as exc:
                print(f"[repair] selective retry failed: {exc}")
            reconciliation=run_reconciliation("선택 재분석 결과를 최종 확인하고 있습니다.")

        # A final cached retry covers odd cases such as a Point row committed but an
        # asset response being lost late in the job (the W1-P2/W5-P9 type symptom).
        final_cached=[mid for mid in reconciliation.get("incomplete_ids",[]) if mid in record_cache]
        if final_cached:
            set_job(job_id,phase="repair",progress=98,message=f"최종 누락 {len(final_cached)} Point 저장을 한 번 더 복구합니다.")
            for mid in final_cached:
                try:
                    save_point(record_cache[mid])
                    recovered_ids.add(mid)
                except Exception as exc:
                    meta=manifest_by_id.get(mid,{})
                    failure_map[mid]={
                        "id":mid,"power":f"{meta.get('power',0)}W","time":f"{meta.get('time',0)}s",
                        "wafer":meta.get("wafer"),"point":meta.get("point"),"sequence_index":meta.get("sequence_index"),
                        "stage":getattr(exc,"stage","final_repair"),"error":f"{type(exc).__name__}: {exc}",
                    }
            reconciliation=run_reconciliation("최종 저장 상태를 검증하고 있습니다.")

        # Reconciliation issues are surfaced even if no callback exception existed.
        for issue in reconciliation.get("incomplete") or []:
            mid=issue.get("id")
            if mid not in failure_map:
                failure_map[mid]={
                    "id":mid,"wafer":issue.get("wafer"),"point":issue.get("point"),
                    "sequence_index":issue.get("sequence_index"),"stage":"reconciliation",
                    "error":"Final storage incomplete: " + "; ".join(issue.get("missing") or ["unknown"]),
                }

        detected=int(structure_info.get("detected_points") or 0)
        missing=int(structure_info.get("missing_count") or max(0,expected_points-detected))
        final_count=int(reconciliation.get("complete_count") or 0)
        complete=(
            final_count==expected_points and detected==expected_points and missing==0 and
            structure_info.get("mapping_safe",True) and not reconciliation.get("incomplete")
        )

        if final_count == 0:
            final_status="failed"; project_status="Failed"
            final_message=f"분석 실패 · 최종 저장 검증 0/{expected_points} · PDF 인식 {detected}/{expected_points}."
        elif complete:
            final_status="completed"; project_status="Ready"
            if recovered_ids:
                final_message=f"완료 · {expected_points}/{expected_points} Point 및 이미지 저장 검증 완료 · 자동 복구 {len(recovered_ids)} Point"
            else:
                final_message=f"완료 · {expected_points}/{expected_points} Point 및 이미지 저장 검증 완료"
        else:
            final_status="partial"; project_status="Partial"
            incomplete_ids=reconciliation.get("incomplete_ids") or []
            final_message=(
                f"부분 완료 · 최종 저장 검증 {final_count}/{expected_points} · "
                f"미완성 {len(incomplete_ids)} Point"
                + (f" ({', '.join(incomplete_ids[:6])}{'…' if len(incomplete_ids)>6 else ''})" if incomplete_ids else "")
            )

        if db.configured():
            db.update_project(project_id,status=project_status)

        PROJECTS[project_id] = {
            "id": project_id,
            "condition_count": len(conditions),
            "conditions": conditions,
            "pages_per_point": pages_per_point,
            "files": saved,
            "records": list(saved_by_source.values()),
            "position_substrates": position_substrates,
            "substrate_type": substrate_type,
            "repeat_no": repeat_no,
            "analysis_failures": _unresolved_failures(),
            "recovered_points": sorted(recovered_ids),
            "point_structure": dict(structure_info),
            "reconciliation": reconciliation,
            "manifest": manifest,
        }

        set_job(
            job_id,
            status=final_status,
            phase="complete" if final_status=="completed" else ("partial" if final_status=="partial" else "error"),
            progress=100 if final_status=="completed" else 99,
            completed=final_count, total=expected_points,
            project_id=project_id, count=final_count,
            detected_points=detected, missing_count=missing,
            missing_points=structure_info.get("missing_points") or [],
            recognition_mode=structure_info.get("recognition_mode"),
            mapping_safe=structure_info.get("mapping_safe",True), manifest_locked=True,
            failed_count=len(failure_map), failed_points=_unresolved_failures(),
            recovered_count=len(recovered_ids), recovered_points=sorted(recovered_ids),
            incomplete_points=reconciliation.get("incomplete") or [],
            reconciliation_complete_count=final_count,
            message=final_message,
            error=(_unresolved_failures()[-1]["error"] if final_status=="failed" and _unresolved_failures() else None),
            storage_warning="원본 PDF의 R2 저장에 실패했습니다. 원본 재분석은 사용할 수 없을 수 있습니다." if storage_warnings else "",
        )
    except Exception as e:
        traceback.print_exc()
        if db.configured():
            try: db.update_project(project_id,status="Failed")
            except Exception: pass
        current=get_job(job_id)
        set_job(
            job_id,status="failed",phase="error",
            error=str(e),
            completed=current.get("completed",0),
            total=current.get("total",0) or 0,
            failed_count=current.get("failed_count",0),
            failed_points=current.get("failed_points",[]),
            incomplete_points=current.get("incomplete_points",[]),
            message=f"분석/저장 중단: {e}. 이미 검증 완료된 Point는 유지됩니다."
        )

def upload_queue_worker():
    while True:
        task=UPLOAD_QUEUE.get()
        if task is None:
            UPLOAD_QUEUE.task_done(); break
        try:
            if task["project_id"] in CANCELLED_PROJECTS:
                set_job(task["job_id"],status="cancelled",phase="cancelled",progress=0,message="삭제 요청으로 작업이 취소되었습니다.")
                continue
            process_upload_job(**task)
        except Exception as e:
            traceback.print_exc()
            set_job(task["job_id"],status="failed",phase="error",progress=0,error=str(e),message=f"분석 실패: {e}")
        finally:
            ANALYSIS_SLOT.release()
            UPLOAD_QUEUE.task_done()

threading.Thread(target=upload_queue_worker,daemon=True,name="uvtape-analysis-queue").start()

def register_upload(
    files: list[UploadFile] = File(...),
    conditions_json: str = Form(...),
    pages_per_point: int = Form(3),
    substrate_type: str = Form("SiCN"),
    position_substrates_json: str = Form("{}"),
    sample_category: str = Form("MAIN"),
    treatment: str = Form("CMP"),
):
    """Receive source PDFs, validate mapping, then process them in the background."""
    if len(files)!=1 or Path(files[0].filename or "").suffix.lower()!=".pdf":
        raise HTTPException(400,"PDF는 한 번에 하나만 업로드하세요.")
    try:
        conditions = json.loads(conditions_json)
        if not isinstance(conditions, list):
            raise ValueError("conditions_json must be a list")
        normalized = normalize_conditions(conditions)
    except Exception as e:
        raise HTTPException(400, f"Invalid condition settings: {e}")
    if pages_per_point < 1:
        raise HTTPException(400, "pages_per_point must be at least 1")
    try:
        position_substrates = normalize_position_substrates(position_substrates_json)
    except Exception as e:
        raise HTTPException(400, str(e))

    if not db.configured() or not r2.configured:
        raise HTTPException(503,"새 분석에는 Supabase와 R2 설정이 모두 필요합니다. 이미지와 결과 저장 설정을 확인하세요.")
    sequence=condition_point_sequence(normalized)
    keys=[(m["power"],m["time"],m["wafer"],m["point"]) for m in sequence]
    if len(keys)!=len(set(keys)):
        raise HTTPException(400,"같은 PDF 안에 중복된 Power/Time/Wafer/Point 조건이 있습니다.")
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
            signature = json.dumps({"substrate_type": substrate_type, "position_substrates": position_substrates, "conditions": normalized}, sort_keys=True, separators=(",", ":"))
            for row in existing:
                try:
                    meta = json.loads(row.get("description") or "{}")
                except Exception:
                    continue
                old_sig = json.dumps({"substrate_type": meta.get("substrate_type", "SiCN"), "position_substrates": normalize_position_substrates(meta.get("position_substrates") or {}), "conditions": meta.get("conditions", [])}, sort_keys=True, separators=(",", ":"))
                if old_sig == signature:
                    repeat_no = max(repeat_no, int(meta.get("repeat_no", 0) or 0) + 1)
                    old_hashes = meta.get("source_hashes") or {}
                    old_keys = meta.get("source_keys") or {}
                    for name, sha in source_hashes.items():
                        if old_hashes.get(name) == sha and old_keys.get(name):
                            source_reuse[name] = old_keys[name]
        except Exception as e:
            print(f"[repeat] lookup failed: {e}")
    metadata={"files": saved, "conditions": conditions, "substrate_type": substrate_type, "position_substrates": position_substrates, "sample_category": sample_category, "repeat_no": repeat_no, "pages_per_point": pages_per_point, "source_hashes": source_hashes, "source_keys": source_reuse, "queue_job_id": job_id, "queue_status": "queued"}
    if db.configured():
        try:
            db.create_project(project_id, f"UV Tape Residue · {substrate_type} · Repeat {repeat_no}", json.dumps(metadata, ensure_ascii=False), status="Queued")
        except Exception as e:
            shutil.rmtree(pdir, ignore_errors=True)
            raise HTTPException(503, f"Project queue registration failed: {e}")
    set_job(job_id, persist_upload=True, files=saved, status="queued", phase="queued", progress=5, completed=0,
            total=expected_points, project_id=project_id,
            message=f"파일 업로드 완료 · Repeat {repeat_no} · 분석 대기열에 추가되었습니다.")
    UPLOAD_QUEUE.put({"job_id":job_id,"project_id":project_id,"pdir":pdir,"pdf_paths":pdf_paths,"saved":saved,"source_hashes":source_hashes,"source_reuse":source_reuse,"conditions":conditions,"pages_per_point":pages_per_point,"substrate_type":substrate_type,"position_substrates":position_substrates,"sample_category":sample_category,"treatment":treatment,"repeat_no":repeat_no})

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
        "position_substrates": position_substrates,
        "sample_category": sample_category,
        "treatment": treatment,
    }

@app.post("/api/upload")
def upload(files: list[UploadFile]=File(...), conditions_json: str=Form(...),
           pages_per_point: int=Form(3), substrate_type: str=Form("SiCN"),
           position_substrates_json: str=Form("{}"), sample_category: str=Form("MAIN"), treatment: str=Form("CMP")):
    if not ANALYSIS_SLOT.acquire(blocking=False):
        raise HTTPException(409,"현재 PDF 분석 또는 재분석이 진행 중입니다. 완료 후 다음 PDF를 올려주세요.")
    try:
        return register_upload(files,conditions_json,pages_per_point,substrate_type,position_substrates_json,sample_category,treatment)
    except BaseException:
        ANALYSIS_SLOT.release()
        raise


def persisted_upload_job(project_id,job_id=None):
    if not db.configured() or not project_id:
        return None
    project=db.get_project(project_id)
    meta=json.loads(project.get("description") or "{}")
    saved=meta.get("upload_job")
    if not saved or (job_id and saved.get("job_id")!=job_id):
        return None
    if saved.get("status") in {"queued","processing"}:
        saved={**saved,"status":"interrupted","phase":"interrupted",
               "message":"서버가 재시작되어 분석이 중단되었습니다. 저장 완료 Point는 유지됩니다. 자동 재개는 수행하지 않습니다."}
    return saved


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, project_id: str=None):
    job=get_job(job_id)
    if not job:
        try: job=persisted_upload_job(project_id,job_id)
        except Exception as exc: raise HTTPException(503,"작업 상태 저장소에 연결할 수 없습니다.") from exc
    if not job: raise HTTPException(404,"job not found")
    return job


@app.get("/api/analysis/current")
def current_upload():
    with JOB_LOCK:
        jobs=[{**v,"job_id":k} for k,v in JOBS.items() if v.get("persist_upload")]
    if jobs:
        return {"job":jobs[-1]}
    if db.configured():
        try:
            project=db.get_latest_project()
            return {"job":persisted_upload_job(project["id"]) if project else None}
        except Exception as exc: raise HTTPException(503,"최근 분석 상태를 불러올 수 없습니다.") from exc
    return {"job":None}

def _r2_client():
    import boto3
    client=boto3.client("s3",endpoint_url=os.getenv("R2_ENDPOINT_URL"),aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),region_name="auto")
    return client,os.getenv("R2_BUCKET_NAME")

def _download_r2_source(key: str, destination: Path):
    client,bucket=_r2_client(); destination.parent.mkdir(parents=True,exist_ok=True); client.download_file(bucket,key,str(destination))


def process_reanalysis_job(job_id,project_id,meta):
    try:
        conditions=meta.get("conditions") or []
        pages_per_point=int(meta.get("pages_per_point",3) or 3)
        files=meta.get("files") or []
        keys=meta.get("source_keys") or {}
        position_substrates=normalize_position_substrates(meta.get("position_substrates") or {})
        if not files:
            raise ValueError("Stored source PDF metadata was not found.")

        temp=UPLOAD/"_reanalyze"/project_id
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True,exist_ok=True)
        pdf_paths=[]
        for name in files:
            key=keys.get(name) or f"projects/{project_id}/source/{name}"
            local=temp/Path(name).name
            _download_r2_source(key,local)
            pdf_paths.append(local)

        expected=len(condition_point_sequence(normalize_conditions(conditions)))
        failures=[]
        structure_info={"detected_points":None,"missing_count":None,"missing_points":[],"recognition_mode":None}

        def structure_cb(info):
            structure_info.update(info or {})
            detected=int(structure_info.get("detected_points") or 0)
            set_job(
                job_id,status="processing",phase="analysis",progress=10,
                completed=0,total=expected,detected_points=detected,
                missing_count=int(structure_info.get("missing_count") or 0),
                missing_points=structure_info.get("missing_points") or [],
                recognition_mode=structure_info.get("recognition_mode"),
                failed_count=0,failed_points=[],
                message=f"PDF Point 구성 확인 · {detected}/{expected} Points 인식됨 · 재분석을 시작합니다."
            )

        def failure_cb(failure):
            failures.append(failure)
            set_job(
                job_id,phase="analysis",failed_count=len(failures),
                failed_points=list(failures),
                message=f"{failure.get('id')} 실패 · 다음 Point 재분석 계속 · 실패 {len(failures)}"
            )

        def point_progress(done,total,phase):
            set_job(
                job_id,phase="analysis",
                progress=min(75,10+int(done/max(total,1)*65)),
                processed=done,total=expected,
                failed_count=len(failures),failed_points=list(failures),
                message=f"재분석 중 · 처리 {done}/{total} · 실패 {len(failures)}"
            )

        set_job(job_id,status="processing",phase="analysis",progress=9,completed=0,total=expected,message="PDF Point 구조를 확인하고 있습니다.")

        gt_examples=[]
        if db.configured():
            try:
                gt_examples=db.get_ground_truth_examples(project_id, limit=8)
            except Exception as e:
                print(f"[ground_truth] reanalysis context lookup failed: {e}")

        records=extract_pdfs(
            pdf_paths,temp/"assets",conditions,pages_per_point,point_progress,
            roi_reference_examples=gt_examples,
            position_substrates=position_substrates,
            point_error_callback=failure_cb,
            structure_callback=structure_cb,
        )

        set_job(job_id,phase="database",progress=76,completed=0,total=expected,message="재분석 결과를 기존 Point에 반영하고 있습니다.")
        saved_count=0
        for i,r in enumerate(records,1):
            source_point_id=r["id"]
            if r2.configured:
                for asset_type,local_path in list(r.get("assets",{}).items()):
                    lp=Path(local_path)
                    key=f"projects/{project_id}/points/{source_point_id}/{asset_type}{lp.suffix.lower() or '.jpg'}"
                    r2.upload_file(lp,key,"image/jpeg")
                    r.setdefault("r2_assets",{})[asset_type]=key
            if db.configured():
                pm=re.search(r"\d+",r["power"])
                tm=re.search(r"\d+",r["time"])
                row=db.get_point_by_key(project_id,int(pm.group()),int(tm.group()),int(r["wafer"]),int(r["point"])) if pm and tm else None
                if not row:
                    row=db.upsert_point(project_id,r)
                    old_analysis=None
                else:
                    old_analysis=db.get_analysis(str(row["id"])) if db.configured() else None
                    r["human_result"]=row.get("human_result")
                    r["human_confidence"]=row.get("human_confidence")
                    r["human_verified_at"]=row.get("human_verified_at")
                    r["human_updated_at"]=row.get("human_updated_at")
                    r["ai_result"]=row.get("ai_result")
                    r["ai_confidence"]=row.get("ai_confidence")
                    r["ai_rationale"]=row.get("ai_rationale")
                    oldf=(old_analysis or {}).get("features") or {}
                    old_polys=oldf.get("human_roi_polygons") or oldf.get("human_roi_polygon")
                    if old_polys:
                        newf=r.setdefault("features",{})
                        for k,v in oldf.items():
                            if k.startswith("human_"):
                                newf[k]=v
                        newf["human_roi_polygons"]=oldf.get("human_roi_polygons") or ([oldf.get("human_roi_polygon")] if oldf.get("human_roi_polygon") else [])
                        newf["human_roi_polygon"]=(oldf.get("human_roi_polygon") or newf["human_roi_polygons"][0])
                        newf["human_reanalysis_preserved"]=True
                        if oldf.get("human_roi_rule_result"):
                            newf["result"]=oldf.get("human_roi_rule_result")
                            newf["confidence"]="Human ROI"
                            newf["residue_score"]=oldf.get("human_residue_score",newf.get("residue_score"))
                r["db_id"]=str(row["id"])
                r["id"]=str(row["id"])
                db.upsert_analysis(r["db_id"],r.get("features",{}))
                for asset_type,key in r.get("r2_assets",{}).items():
                    db.upsert_asset(r["db_id"],asset_type,key)

            RECORDS[r["id"]]=r
            saved_count+=1
            set_job(
                job_id,phase="database",
                progress=min(99,76+int(i/max(len(records),1)*23)),
                completed=saved_count,total=expected,
                failed_count=len(failures),failed_points=list(failures),
                message=f"재분석 결과 저장 중 · {saved_count}/{expected}"
            )

        detected=int(structure_info.get("detected_points") or 0)
        missing=int(structure_info.get("missing_count") or max(0,expected-detected))
        partial=bool(failures or missing or saved_count<expected)
        final_status="partial" if partial else "completed"
        if partial:
            message=f"재분석 부분 완료 · 저장 {saved_count}/{expected} · 실패 {len(failures)} · PDF 인식 {detected}/{expected}"
        else:
            message="기존 데이터 재분석이 완료되었습니다."

        shutil.rmtree(temp,ignore_errors=True)
        set_job(
            job_id,status=final_status,phase="partial" if partial else "complete",
            progress=99 if partial else 100,completed=saved_count,total=expected,
            project_id=project_id,count=saved_count,
            detected_points=detected,missing_count=missing,
            missing_points=structure_info.get("missing_points") or [],
            recognition_mode=structure_info.get("recognition_mode"),
            failed_count=len(failures),failed_points=list(failures),
            message=message
        )
    except Exception as e:
        traceback.print_exc()
        set_job(job_id,status="failed",phase="error",progress=0,error=str(e),message=f"재분석 실패: {e}")

@app.post("/api/projects/{project_id}/reanalyze")
def reanalyze_project(project_id: str):
    if not db.configured() or not r2.configured: raise HTTPException(503,"Supabase/R2 is not configured.")
    try: p=db.get_project(project_id)
    except Exception: raise HTTPException(404,"project not found")
    try: meta=json.loads(p.get("description") or "{}")
    except Exception: meta={}
    if not meta.get("files"): raise HTTPException(400,"Stored source PDF metadata was not found for this project.")
    job_id=str(uuid.uuid4()); expected=len(condition_point_sequence(normalize_conditions(meta.get("conditions") or [])))
    if not ANALYSIS_SLOT.acquire(blocking=False):
        raise HTTPException(409,"현재 PDF 분석 또는 재분석이 진행 중입니다.")
    def run_reanalysis():
        try: process_reanalysis_job(job_id,project_id,meta)
        finally: ANALYSIS_SLOT.release()
    try:
        set_job(job_id,status="queued",phase="queued",progress=5,completed=0,total=expected,project_id=project_id,message="R2에 저장된 원본 PDF를 불러오는 중입니다.")
        threading.Thread(target=run_reanalysis,daemon=True).start()
    except BaseException:
        ANALYSIS_SLOT.release()
        raise
    return {"job_id":job_id,"project_id":project_id,"expected_points":expected}

@app.get("/api/projects")
def list_projects():
    """Return project history so uploading a new dataset does not hide older projects."""
    if not db.configured():
        rows=[]
        for pid,p in PROJECTS.items():
            rows.append({"id":pid,"name":p.get("name") or "UV Tape Residue","created_at":None,"point_count":len(p.get("records") or [])})
        return {"projects":rows}
    try:
        rows=db.get_projects(limit=100)
        out=[]
        for row in rows:
            try: meta=json.loads(row.get("description") or "{}")
            except Exception: meta={}
            try: count=db.get_project_point_count(row["id"])
            except Exception: count=0
            out.append({"id":row["id"],"name":row.get("name") or "UV Tape Residue","created_at":row.get("created_at"),"updated_at":row.get("updated_at"),"status":row.get("status"),"point_count":count,"repeat_no":meta.get("repeat_no"),"files":meta.get("files") or [],"conditions":meta.get("conditions") or [],"substrate_type":meta.get("substrate_type") or "SiCN","position_substrates":normalize_position_substrates(meta.get("position_substrates") or {})})
        return {"projects":out}
    except Exception as e:
        print(f"[projects] Supabase lookup failed: {type(e).__name__}: {e}")
        raise HTTPException(503,"Project history could not be loaded.")


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    if project_id in CANCELLED_PROJECTS:
        pass
    CANCELLED_PROJECTS.add(project_id)
    job_ids=[]
    with JOB_LOCK:
        for jid,job in JOBS.items():
            if job.get("project_id")==project_id and job.get("status") not in ("completed","failed","cancelled"):
                job_ids.append(jid)
                job.update(status="cancelled",phase="cancelled",message="프로젝트 삭제로 작업이 취소되었습니다.")
    old_project=PROJECTS.get(project_id,{})
    old_record_ids=set(old_project.get("records") or [])
    if db.configured():
        try:
            db.delete_project(project_id)
        except Exception as e:
            CANCELLED_PROJECTS.discard(project_id)
            raise HTTPException(500,f"프로젝트 삭제 실패: {e}")
    if r2.configured:
        try: r2.delete_prefix(f"projects/{project_id}/")
        except Exception as e: print(f"[delete] R2 cleanup failed: {e}")
    shutil.rmtree(UPLOAD/project_id, ignore_errors=True)
    PROJECTS.pop(project_id,None)
    for rid in old_record_ids:
        RECORDS.pop(rid,None)
    return {"ok":True,"project_id":project_id,"cancelled_jobs":job_ids}

@app.get("/api/workspace")
def workspace():
    """Return all stored points as one cumulative analysis workspace.

    Projects remain internal batches for queueing, source-file ownership,
    re-analysis, and selective deletion, but the user-facing dataset is
    intentionally cumulative. This also makes older uploads reappear without
    requiring the PDF to be uploaded again.
    """
    if not db.configured():
        all_recs=[]
        for pid,p in PROJECTS.items():
            for rid in p.get("records") or []:
                if rid in RECORDS: all_recs.append(RECORDS[rid])
        all_recs.sort(key=lambda r:(str(r.get("power","")),str(r.get("time","")),int(r.get("wafer",0) or 0),int(r.get("point",0) or 0)))
        latest=next(reversed(list(PROJECTS.keys())),None) if PROJECTS else None
        return {"project_id":latest,"latest_project_id":latest,"points":[public_record(r) for r in all_recs]}
    try:
        packed=db.get_all_points_with_data()
        projects=db.get_projects(limit=1000)
        latest=projects[0] if projects else None
    except Exception as e:
        print(f"[workspace] Supabase lookup failed: {type(e).__name__}: {e}")
        raise HTTPException(503,"Cumulative workspace data could not be loaded.")
    all_recs=[]
    for row,analysis,assets in packed:
        pid=str(row.get("project_id"))
        rec=db_record(pid,row,analysis,assets)
        rec["project_id"]=pid
        all_recs.append(rec)
        RECORDS[rec["id"]]=rec
    all_recs.sort(key=lambda r:(int(re.search(r"\d+",str(r.get("power","0"))).group()) if re.search(r"\d+",str(r.get("power","0"))) else 0, int(re.search(r"\d+",str(r.get("time","0"))).group()) if re.search(r"\d+",str(r.get("time","0"))) else 0, int(r.get("wafer",0) or 0), int(r.get("point",0) or 0), str(r.get("id"))))
    latest_id=str(latest["id"]) if latest else None
    latest_meta={}
    if latest:
        try: latest_meta=json.loads(latest.get("description") or "{}")
        except Exception: latest_meta={}
    return {
        "project_id":latest_id,
        "latest_project_id":latest_id,
        "points":[public_record(r) for r in all_recs],
        "position_substrates":normalize_position_substrates(latest_meta.get("position_substrates") or {}),
        "substrate_type":latest_meta.get("substrate_type") or "SiCN",
        "project_count":len(projects),
    }

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
    try: meta=json.loads(p.get("description") or "{}")
    except Exception: meta={}
    PROJECTS[project_id] = {
        "id": project_id,
        "condition_count": len(meta.get("conditions") or []),
        "conditions": meta.get("conditions") or [],
        "files": meta.get("files") or [],
        "records": [r["id"] for r in recs],
        "position_substrates": normalize_position_substrates(meta.get("position_substrates") or {}),
        "substrate_type": meta.get("substrate_type") or "SiCN",
        "repeat_no": meta.get("repeat_no"),
    }
    return {"project_id": project_id, "points": [public_record(r) for r in recs], "position_substrates": PROJECTS[project_id]["position_substrates"], "substrate_type": PROJECTS[project_id]["substrate_type"]}

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
    try: meta=json.loads(p.get("description") or "{}")
    except Exception: meta={}
    PROJECTS[project_id]={"id":project_id,"condition_count":len(meta.get("conditions") or []),"conditions":meta.get("conditions") or [],"files":meta.get("files") or [],"records":[r["id"] for r in recs],"position_substrates":normalize_position_substrates(meta.get("position_substrates") or {}),"substrate_type":meta.get("substrate_type") or "SiCN","repeat_no":meta.get("repeat_no")}
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
    """Generate and cache Verification overlays.

    Human-ROI path is intentionally lightweight: after a manual ROI is saved,
    the ROI mask is already known, so do NOT rerun the full residue_features()
    pipeline (which loads C/O/N/Si) for every SEM/C/O image request.  Build only
    the source image needed for the requested overlay and apply the saved mask.
    Legacy/AI-CV points keep the previous full pipeline for compatibility.
    """
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
    rr = RECORDS.get(point_id) or {}
    stored_features = rr.get("features") or {}
    stored_polygons = stored_features.get("human_roi_polygons") or stored_features.get("human_roi_polygon")

    def load(label, aliases=()):
        local_assets = rr.get("assets") or {}
        local_path = local_assets.get(label)
        if local_path and Path(local_path).exists():
            im = cv2.imread(str(local_path), cv2.IMREAD_COLOR)
            if im is not None and im.size:
                return im
        key = keys.get(label)
        if not key:
            for alias in aliases:
                if keys.get(alias):
                    key = keys[alias]
                    break
        if not key or not r2.configured:
            return None
        cache = outdir / f"raw_{label}.jpg"
        try:
            if not cache.exists() or cache.stat().st_size < 500:
                r2.download_file(key, cache)
            im = cv2.imread(str(cache), cv2.IMREAD_COLOR)
            if im is not None and im.size:
                return im
            # Remove a stale/corrupt cache so the next request can redownload it.
            cache.unlink(missing_ok=True)
            r2.download_file(key, cache)
            im = cv2.imread(str(cache), cv2.IMREAD_COLOR)
            return im if im is not None and im.size else None
        except Exception:
            return None

    from .point_worker import make_box_overlay, make_co_overlay, enhance_element_map, polygon_to_mask

    # Fast path for saved Human ROI: only load the image(s) actually required.
    if stored_polygons:
        sem = load("sem", ("sem_original", "sem_residue_overlay"))
        if sem is None:
            return None
        try:
            roi = polygon_to_mask(sem.shape, stored_polygons)
        except Exception:
            return None
        if cv2.countNonZero(roi) < 20:
            return None

        if asset_type == "sem_residue_overlay":
            out = make_box_overlay(sem, roi, roi_color=(0, 0, 255))
        elif asset_type == "c_map_enhanced_overlay":
            src = load("c_map")
            if src is None:
                return None
            rr_mask = cv2.resize(roi, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_NEAREST)
            out = make_box_overlay(enhance_element_map(src, "C"), rr_mask, roi_color=(255, 255, 255))
        elif asset_type == "o_map_enhanced_overlay":
            src = load("o_map")
            if src is None:
                return None
            rr_mask = cv2.resize(roi, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_NEAREST)
            out = make_box_overlay(enhance_element_map(src, "O"), rr_mask, roi_color=(255, 255, 255))
        elif asset_type == "eds_co_overlay":
            c = load("c_map")
            o = load("o_map")
            if c is None or o is None:
                return None
            out = make_co_overlay(c, o, cv2.resize(roi, (c.shape[1], c.shape[0]), interpolation=cv2.INTER_NEAREST))
        else:
            return None

        if out is None:
            return None
        cv2.imwrite(str(target), out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        return str(target) if target.exists() else None

    # Legacy/AI-CV compatibility path: retain the previous full calculation.
    from .point_worker import residue_features, detect_residue_candidates
    sem = load("sem")
    if sem is None:
        sem = load("sem_residue_overlay")
    if sem is None:
        return None
    c = load("c_map")
    o = load("o_map")
    n = load("n_map")
    si = load("si_map")
    if c is not None and o is not None and n is not None and si is not None:
        try:
            features, roi = residue_features(sem, c, o, n, si)
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
            return None
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
    # Human ROI becomes the active analysis basis immediately after SAVE.
    # Keep the original AI/CV values separately for later ROI-quality comparison.
    if "ai_cv_result_before_human" not in f:
        f["ai_cv_result_before_human"] = f.get("result")
    if "ai_cv_score_before_human" not in f:
        f["ai_cv_score_before_human"] = f.get("residue_score")
    f["residue_score"] = hf.get("human_residue_score")
    f["result"] = hf.get("human_roi_rule_result")
    f["confidence"] = "Human ROI"
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
    rec=[public_record(RECORDS[x]) for x in PROJECTS[project_id]["records"]]
    from .ai import analyze_project_with_openai
    return analyze_project_with_openai(rec)



def workspace_records_for_export():
    """Return cumulative raw records for engineering summary exports."""
    if not db.configured():
        out=[]
        for pid,proj in PROJECTS.items():
            for rid in proj.get("records") or []:
                if rid in RECORDS:
                    out.append(RECORDS[rid])
        return out
    packed=db.get_all_points_with_data()
    out=[]
    for row,analysis,assets in packed:
        pid=str(row.get("project_id"))
        rec=db_record(pid,row,analysis,assets)
        rec["project_id"]=pid
        out.append(rec)
    return out

@app.post("/api/workspace/export/summary-ppt")
def workspace_summary_ppt():
    from .reports import export_engineering_ppt
    rec=workspace_records_for_export()
    out=OUTPUT/"workspace_engineering_summary.pptx"
    export_engineering_ppt(rec,out)
    if r2.configured:
        try: r2.upload_file(out,"workspace/reports/workspace_engineering_summary.pptx","application/vnd.openxmlformats-officedocument.presentationml.presentation")
        except Exception as e: print(f"[summary-export] R2 PPT upload failed: {e}")
    return FileResponse(out,filename=out.name,media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

@app.post("/api/workspace/export/summary-pdf")
def workspace_summary_pdf():
    from .reports import export_engineering_pdf
    rec=workspace_records_for_export()
    out=OUTPUT/"workspace_engineering_summary.pdf"
    export_engineering_pdf(rec,out)
    if r2.configured:
        try: r2.upload_file(out,"workspace/reports/workspace_engineering_summary.pdf","application/pdf")
        except Exception as e: print(f"[summary-export] R2 PDF upload failed: {e}")
    return FileResponse(out,filename=out.name,media_type="application/pdf")

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
