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
    model=os.getenv("OPENAI_MODEL","gpt-5.6-luna")
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
    model=os.getenv("OPENAI_MODEL","gpt-5.6-luna")
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


ROI_SYSTEM = """You are a SEM image assistant for UV-tape residue analysis.
Your only task is to propose candidate residue regions in a SEM image.
Do NOT classify the point as Residue or Non-residue.
Return only JSON.
Important visual rules:
- Ignore the bottom acquisition-information / scale-bar strip.
- Ignore the thin image/page border.
- The SEM background is non-uniform, so judge objects by local shape/contrast, not one global gray threshold.
- Prefer smooth connected physical-looking regions over isolated single-pixel speckles or random grain noise.
- Include large, faint, elongated, irregular, or multiple separated residue regions when they look like real objects.
- Do not include obvious background texture, isolated noise dots, scale bars, labels, or border artifacts.
- Return normalized boxes [x, y, width, height] where all values are 0..1 relative to the full image.
- A box is only a coarse guide for the CV segmentation stage; it does not become the final ROI itself.
- At most 8 boxes. If no defensible object exists, return an empty list.
"""

def analyze_roi_boxes_with_openai(image_path, cv_features=None):
    """Use OpenAI vision only as a second-stage ROI proposal, never as the classifier."""
    if not ai_available():
        return {"status":"not_configured","boxes":[],"used":False}
    try:
        from PIL import Image
        import io, base64
        img=Image.open(image_path).convert("RGB")
        # Keep vision input reasonably small so this remains inexpensive and fast.
        max_side=1400
        scale=min(1.0, max_side/max(img.size))
        if scale < 1.0:
            img=img.resize((max(1,int(img.width*scale)),max(1,int(img.height*scale))))
        buf=io.BytesIO(); img.save(buf,format="JPEG",quality=82,optimize=True)
        data_url="data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode("ascii")
        client=OpenAI()
        model=os.getenv("OPENAI_ROI_MODEL",os.getenv("OPENAI_MODEL","gpt-5.6-luna"))
        hint={
            "cv_result":(cv_features or {}).get("result"),
            "cv_confidence":(cv_features or {}).get("confidence"),
            "candidate_count":(cv_features or {}).get("candidate_count"),
            "selected_candidate_count":(cv_features or {}).get("selected_candidate_count"),
            "morphology_score":(cv_features or {}).get("morphology_score"),
        }
        prompt=("Inspect this SEM image and propose coarse residue-region boxes for a downstream OpenCV segmentation stage. "
                "Do not decide Residue/Non-residue. "
                "Use the CV hints only as context; visually correct them when they appear incomplete. "
                "Return JSON exactly as {\"boxes\":[{\"x\":0..1,\"y\":0..1,\"w\":0..1,\"h\":0..1,\"confidence\":0..1}],\"notes\":\"...\"}.\n"
                +json.dumps(hint,ensure_ascii=False))
        response=client.responses.create(
            model=model,
            instructions=ROI_SYSTEM,
            input=[{"role":"user","content":[
                {"type":"input_text","text":prompt},
                {"type":"input_image","image_url":data_url,"detail":"high"},
            ]}],
            text={"format":{"type":"json_object"}},
        )
        raw=getattr(response,"output_text","") or "{}"
        parsed=json.loads(raw)
        boxes=[]
        for b in parsed.get("boxes",[]) if isinstance(parsed,dict) else []:
            try:
                x=float(b.get("x")); y=float(b.get("y")); w=float(b.get("w")); h=float(b.get("h")); conf=float(b.get("confidence",0.5))
                x=max(0,min(1,x)); y=max(0,min(1,y)); w=max(0,min(1-x,w)); h=max(0,min(1-y,h)); conf=max(0,min(1,conf))
                if w>=0.008 and h>=0.008:
                    boxes.append({"x":x,"y":y,"w":w,"h":h,"confidence":conf})
            except Exception:
                continue
        return {"status":"completed","used":bool(boxes),"boxes":boxes[:8],"notes":str(parsed.get("notes","") if isinstance(parsed,dict) else "")}
    except Exception as e:
        return {"status":"error","used":False,"boxes":[],"message":f"OpenAI ROI assist failed: {type(e).__name__}: {e}"}
