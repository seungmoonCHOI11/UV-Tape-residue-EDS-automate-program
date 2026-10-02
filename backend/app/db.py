
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


def get_points_with_data(project_id: str):
    """Load points, latest analysis, and assets with bounded DB requests."""
    client = get_client()
    rows = (client.table("points").select("*").eq("project_id", project_id)
            .order("power").order("time_sec").order("wafer").order("point")
            .execute().data or [])
    if not rows:
        return []
    ids = [str(r["id"]) for r in rows]
    latest_analysis = {}
    asset_map = {}
    for start in range(0, len(ids), 100):
        chunk = ids[start:start+100]
        analyses = (client.table("analysis_results").select("*")
                    .in_("point_id", chunk).order("created_at", desc=True)
                    .execute().data or [])
        for a in analyses:
            pid = str(a["point_id"])
            if pid not in latest_analysis:
                latest_analysis[pid] = a
        assets = (client.table("point_assets").select("point_id,asset_type,storage_path")
                  .in_("point_id", chunk).execute().data or [])
        for a in assets:
            asset_map.setdefault(str(a["point_id"]), {})[a["asset_type"]] = a["storage_path"]
    return [(r, latest_analysis.get(str(r["id"])), asset_map.get(str(r["id"]), {})) for r in rows]


def get_points(project_id: str):
    return get_client().table("points").select("*").eq("project_id", project_id).order("created_at").execute().data or []

def get_point_by_key(project_id: str, power: int, time_sec: int, wafer: int, point: int):
    rows = (get_client().table("points").select("*")
            .eq("project_id", project_id).eq("power", power).eq("time_sec", time_sec)
            .eq("wafer", wafer).eq("point", point).limit(1).execute().data or [])
    return rows[0] if rows else None

def get_analysis(point_id: str):
    rows=get_client().table("analysis_results").select("*").eq("point_id",point_id).order("created_at",desc=True).limit(1).execute().data or []
    return rows[0] if rows else None

def get_assets(point_id: str):
    return get_client().table("point_assets").select("asset_type,storage_path").eq("point_id",point_id).execute().data or []

def re_digits(value: Any) -> str:
    m=re.search(r"\d+",str(value or ""))
    return m.group(0) if m else "0"


def get_ground_truth_examples(project_id: str, limit: int = 12):
    """Return balanced, project-specific Human ROI references for later ROI assistance.

    Human-verified annotations are not used to retrain the base model. They are
    project-level Ground Truth references. Prefer a balanced sample across the
    verified Residue / Ambiguous / Non-residue classes so one class does not
    dominate the guidance when the project grows beyond the first 30 points.
    """
    if not configured():
        return []
    client=get_client()
    rows=(client.table("points").select("id,power,time_sec,wafer,point,position,human_result,updated_at")
          .eq("project_id",project_id).not_.is_("human_result","null")
          .order("updated_at",desc=True).limit(max(30,int(limit)*4)).execute().data or [])
    candidates=[]
    for row in rows:
        try:
            analyses=(client.table("analysis_results").select("features")
                      .eq("point_id",row["id"]).order("created_at",desc=True).limit(1).execute().data or [])
            f=(analyses[0].get("features") or {}) if analyses else {}
            if not (f.get("human_roi_polygons") or f.get("human_roi_polygon")):
                continue
            candidates.append({
                "power":row.get("power"),"time_sec":row.get("time_sec"),
                "wafer":row.get("wafer"),"point":row.get("point"),
                "zone":row.get("position"),"human_result":row.get("human_result"),
                "human_roi_polygons":f.get("human_roi_polygons") or f.get("human_roi_polygon"),
                "human_roi_area_px":f.get("human_roi_area_px"),
                "human_roi_fill_ratio":f.get("human_roi_fill_ratio"),
                "human_roi_component_count":f.get("human_roi_component_count"),
                "human_c_ratio":f.get("human_c_ratio"),"human_o_ratio":f.get("human_o_ratio"),
                "human_roi_quality":f.get("human_roi_quality"),
            })
        except Exception as e:
            print(f"[ground_truth] example lookup failed: {e}")
    if not candidates:
        return []
    # Balanced selection: take up to 4 recent examples per verified class, then
    # fill remaining slots by recency. This lets new Human ROI decisions gradually
    # replace old examples without discarding class diversity.
    selected=[]
    for label in ("Residue","Ambiguous","Non-residue"):
        selected.extend([x for x in candidates if x.get("human_result")==label][:4])
    seen={(x.get("power"),x.get("time_sec"),x.get("wafer"),x.get("point")) for x in selected}
    for x in candidates:
        key=(x.get("power"),x.get("time_sec"),x.get("wafer"),x.get("point"))
        if key not in seen:
            selected.append(x); seen.add(key)
        if len(selected)>=int(limit): break
    return selected[:int(limit)]

def get_first_unverified_point(project_id: str):
    rows = (get_client().table("points").select("*")
            .eq("project_id", project_id)
            .is_("human_result", "null")
            .order("power").order("time_sec").order("wafer").order("point")
            .limit(1).execute().data or [])
    return rows[0] if rows else None

def add_review_history(point_id: str, previous_result, new_result, reviewer_note=None):
    payload={"point_id":point_id,"previous_human_result":previous_result,
             "new_human_result":new_result,"reviewer_note":reviewer_note}
    return get_client().table("point_review_history").insert(payload).execute()
