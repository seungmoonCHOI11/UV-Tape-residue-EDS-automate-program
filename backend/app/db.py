
import os, re
from typing import Any, Optional
from supabase import create_client, Client

_client: Optional[Client] = None

def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not configured.")
        _client = create_client(url, key)
    return _client

def configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

def create_project(project_id: str, name: str, description: str = ""):
    return get_client().table("projects").insert({
        "id": project_id, "name": name, "description": description
    }).execute()

def upsert_point(project_id: str, r: dict):
    power = int(re_digits(r.get("power")))
    time_sec = int(re_digits(r.get("time")))
    payload={
        "project_id": project_id, "power": power,
        "time_sec": time_sec, "wafer": int(r["wafer"]), "point": int(r["point"]),
        "position": r.get("zone"), "human_result": r.get("human_result"),
        "human_confidence": r.get("human_confidence"),
        "ai_result": r.get("ai_result"),
        "ai_confidence": r.get("ai_confidence") or r.get("confidence"),
        "ai_rationale": r.get("ai_rationale"),
    }
    response = get_client().table("points").upsert(payload, on_conflict="project_id,power,time_sec,wafer,point").execute()
    rows = response.data or []
    if not rows or not rows[0].get("id"):
        raise RuntimeError("Supabase point upsert did not return the point UUID.")
    return rows[0]

def upsert_analysis(point_id: str, features: dict):
    client=get_client()
    existing=client.table("analysis_results").select("id").eq("point_id",point_id).order("created_at",desc=True).limit(1).execute().data or []
    payload={
        "residue_score": features.get("residue_score"),
        "c_enrichment": features.get("c_enrichment"),
        "o_enrichment": features.get("o_enrichment"),
        "c_coverage": features.get("c_coverage"),
        "o_coverage": features.get("o_coverage"),
        "cluster_score": features.get("cluster_score"),
        "cv_result": features.get("result"),
        "cv_confidence": features.get("confidence"),
        "features": features,
    }
    if existing:
        return client.table("analysis_results").update(payload).eq("id",existing[0]["id"]).execute()
    return client.table("analysis_results").insert({"point_id":point_id,**payload}).execute()

def upsert_asset(point_id: str, asset_type: str, storage_path: str):
    client=get_client()
    existing=client.table("point_assets").select("id").eq("point_id",point_id).eq("asset_type",asset_type).limit(1).execute().data or []
    payload={"storage_path":storage_path}
    if existing:
        return client.table("point_assets").update(payload).eq("id",existing[0]["id"]).execute()
    return client.table("point_assets").insert({
        "point_id":point_id,"asset_type":asset_type,"storage_path":storage_path
    }).execute()

def update_point(point_id: str, **values):
    return get_client().table("points").update(values).eq("id", point_id).execute()

def get_project(project_id: str):
    return get_client().table("projects").select("*").eq("id", project_id).single().execute().data

def get_latest_project():
    rows = get_client().table("projects").select("*").order("created_at", desc=True).limit(1).execute().data or []
    return rows[0] if rows else None

def get_points(project_id: str):
    return get_client().table("points").select("*").eq("project_id", project_id).order("created_at").execute().data or []

def get_analysis(point_id: str):
    rows=get_client().table("analysis_results").select("*").eq("point_id",point_id).order("created_at",desc=True).limit(1).execute().data or []
    return rows[0] if rows else None

def get_assets(point_id: str):
    return get_client().table("point_assets").select("asset_type,storage_path").eq("point_id",point_id).execute().data or []

def re_digits(value: Any) -> str:
    m=re.search(r"\d+",str(value or ""))
    return m.group(0) if m else "0"
