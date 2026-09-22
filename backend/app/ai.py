
import os, json
from pathlib import Path
from openai import OpenAI
from .models import AIResult
from .analysis import image_to_data_url

SYSTEM = """You are an assistant for SEM/EDS residue analysis.
Do not invent measurements. Use the supplied CV features and images as evidence.
Return a structured judgment of Residue, Non-residue, or Review, with confidence.
Residue means the SEM candidate and C/O evidence jointly support contamination/residue.
If evidence is weak or conflicting, choose Review and request human verification.
Keep rationale concise and cite the supplied feature values in the evidence list."""

def ai_available():
    return bool(os.getenv("OPENAI_API_KEY"))

def analyze_with_openai(record, r2_storage=None):
    if not ai_available():
        return {"status":"not_configured","message":"OPENAI_API_KEY is not set."}
    client=OpenAI()
    model=os.getenv("OPENAI_MODEL","gpt-5.5")
    f=record.get("features",{})
    images=[]
    for key in ("sem","element_maps"):
        path=(record.get("assets") or {}).get(key)
        r2_key=(record.get("r2_assets") or {}).get(key)
        if path and Path(path).exists():
            images.append({"type":"input_image","image_url":image_to_data_url(path),"detail":"low"})
        elif r2_key and r2_storage and r2_storage.configured:
            images.append({"type":"input_image","image_url":r2_storage.presigned_url(r2_key,expires=900),"detail":"low"})
    prompt={
        "point":record.get("id"),
        "condition":f"{record.get('power')} / {record.get('time')}",
        "wafer":record.get("wafer"),
        "point_number":record.get("point"),
        "features":f,
        "instruction":"Classify the point using the evidence. Do not treat pixel brightness as concentration by itself."
    }
    response=client.responses.parse(
        model=model,
        instructions=SYSTEM,
        input=[{"role":"user","content":[
            {"type":"input_text","text":json.dumps(prompt,ensure_ascii=False)},
            *images
        ]}],
        text_format=AIResult
    )
    parsed=response.output_parsed
    return parsed.model_dump() if parsed else {"status":"empty","message":"No structured result returned."}
