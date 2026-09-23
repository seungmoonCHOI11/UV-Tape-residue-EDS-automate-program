import os, json
from pathlib import Path
from openai import OpenAI
from .models import AIResult
from .analysis import image_to_data_url

POINT_SYSTEM = """Legacy point-level helper. Do not use this for the production Residue/Non-residue classification.
If called, treat the CV result as authoritative and only explain the evidence; never override it."""

def ai_available():
    return bool(os.getenv("OPENAI_API_KEY"))

def analyze_with_openai(record, r2_storage=None):
    if not ai_available():
        return {"status":"not_configured","message":"OPENAI_API_KEY is not set."}
    client=OpenAI()
    model=os.getenv("OPENAI_MODEL","gpt-5.5")
    f=record.get("features",{})
    prompt={
        "point":record.get("id"),
        "condition":f"{record.get('power')} / {record.get('time')}",
        "wafer":record.get("wafer"),
        "point_number":record.get("point"),
        "cv_result":f.get("result"),
        "features":f,
        "instruction":"Explain the already-computed CV classification. Do not change Residue/Non-residue."
    }
    response=client.responses.parse(
        model=model, instructions=POINT_SYSTEM,
        input=[{"role":"user","content":[{"type":"input_text","text":json.dumps(prompt,ensure_ascii=False)}]}],
        text_format=AIResult
    )
    parsed=response.output_parsed
    return parsed.model_dump() if parsed else {"status":"empty","message":"No structured result returned."}

PROJECT_SYSTEM = """You are a research assistant analyzing a SEM/EDS residue dataset.
The dataset already contains algorithmic CV classifications and human review fields.
Do NOT make or change the individual Residue/Non-residue classification.
Do not invent measurements, mechanisms, or trends that are not supported by the supplied dataset.
Summarize condition-level patterns, notable points, relationships among residue score and C/O/N-related features, anomalies, and reasonable next experimental checks.
Clearly distinguish observed data from interpretation. Keep recommendations practical for a semiconductor/SEM-EDS research workflow."""

def analyze_project_with_openai(records):
    if not ai_available():
        return {"status":"not_configured","message":"OPENAI_API_KEY is not set."}
    client=OpenAI()
    model=os.getenv("OPENAI_MODEL","gpt-5.5")
    compact=[]
    for r in records:
        f=r.get("features") or {}
        compact.append({
            "id":r.get("id"), "power":r.get("power"), "time":r.get("time"),
            "wafer":r.get("wafer"), "point":r.get("point"), "zone":r.get("zone"),
            "cv_result":f.get("result"), "cv_confidence":f.get("confidence"),
            "human_result":r.get("human_result"),
            "residue_score":f.get("residue_score"),
            "c_enrichment":f.get("c_enrichment"), "o_enrichment":f.get("o_enrichment"),
            "c_coverage":f.get("c_coverage"), "o_coverage":f.get("o_coverage"),
            "n_enrichment":f.get("n_enrichment"), "n_coverage":f.get("n_coverage"),
            "cluster_score":f.get("cluster_score"),
        })
    prompt={"point_count":len(compact),"points":compact,"instruction":"Analyze this dataset at experiment/condition level. Do not relabel individual points."}
    response=client.responses.create(
        model=model,
        instructions=PROJECT_SYSTEM + " Return valid JSON with keys: summary, key_findings, condition_trends, anomalies, next_steps, caveats. Each list should contain concise Korean strings.",
        input=json.dumps(prompt,ensure_ascii=False),
        text={"format":{"type":"json_object"}}
    )
    raw=getattr(response,"output_text","") or "{}"
    try:
        parsed=json.loads(raw)
    except Exception:
        parsed={"summary":raw,"key_findings":[],"condition_trends":[],"anomalies":[],"next_steps":[],"caveats":["OpenAI returned non-JSON text; raw output is shown in summary."]}
    parsed["status"]="completed"
    return parsed
