"""Explicit user-entered experimental identity; upload repeat counters are not specimen IDs."""
import json
from datetime import date,datetime,timezone
from pydantic import BaseModel,Field
from fastapi import HTTPException

class Identity(BaseModel):
    batch_label:str=Field(default='',max_length=80)
    sample_id:str=Field(default='',max_length=80)
    measured_on:date|None=None
    repeat_index:int|None=Field(default=None,ge=1,le=9999)
    notes:str=Field(default='',max_length=500)
    expected_version:str|None=None

def parse_meta(row):
    raw=row.get('description') or '{}'
    try:value=json.loads(raw) if isinstance(raw,str) else raw
    except (ValueError,TypeError):value={}
    return value if isinstance(value,dict) else {}

def attach_identities(records,db,projects):
    rows=db.get_projects(limit=10000) if db.configured() else [dict(p,id=pid) for pid,p in projects.items()]
    by_id={str(p['id']):(parse_meta(p).get('research_identity') or p.get('research_identity') or {}) for p in rows}
    return [{**p,'research_identity':by_id.get(str(p.get('project_id')), {})} for p in records]

def install_identity_routes(app,db,projects,analysis_slot,persist_lock):
    @app.put('/api/projects/{project_id}/research-identity')
    def save_identity(project_id:str,value:Identity):
        if not analysis_slot.acquire(blocking=False):raise HTTPException(409,'분석 또는 보고서가 진행 중입니다. 완료 후 시료 정보를 저장하세요.')
        try:
            with persist_lock:
                try:row=db.get_project(project_id) if db.configured() else projects.get(project_id)
                except Exception as e:raise HTTPException(503,'시료 정보를 읽지 못했습니다.') from e
                if not row:raise HTTPException(404,'업로드 배치를 찾을 수 없습니다.')
                meta=parse_meta(row);old=meta.get('research_identity') or row.get('research_identity') or {}
                if old.get('version')!=value.expected_version:raise HTTPException(409,'다른 변경 사항이 있습니다. 데이터를 새로 불러온 뒤 수정하세요.')
                identity=value.model_dump(mode='json',exclude={'expected_version'})
                for key in ['batch_label','sample_id','notes']:identity[key]=identity[key].strip()
                identity['version']=datetime.now(timezone.utc).isoformat();meta['research_identity']=identity
                try:
                    if db.configured():db.update_project(project_id,description=json.dumps(meta,ensure_ascii=False))
                    if project_id in projects:projects[project_id].update(research_identity=identity,description=json.dumps(meta,ensure_ascii=False))
                except Exception as e:raise HTTPException(503,'시료 정보 저장에 실패했습니다. 기존 정보는 다시 불러와 확인하세요.') from e
                return {'project_id':project_id,'research_identity':identity}
        finally:analysis_slot.release()
