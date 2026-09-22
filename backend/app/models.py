from pydantic import BaseModel, Field
from typing import Optional, List

class PointRecord(BaseModel):
    id: str
    power: str
    time: str
    wafer: int
    point: int
    zone: str
    page: int
    ai_result: Optional[str] = None
    human_result: Optional[str] = None
    confidence: Optional[str] = None
    residue_score: float = 0.0
    c_enrichment: float = 0.0
    o_enrichment: float = 0.0
    c_coverage: float = 0.0
    o_coverage: float = 0.0
    cluster_score: float = 0.0
    element_data: dict = Field(default_factory=dict)
    assets: dict = Field(default_factory=dict)
    analysis_notes: List[str] = Field(default_factory=list)

class AIResult(BaseModel):
    result: str
    confidence: str
    rationale: str
    evidence: List[str]
    caveats: List[str]
    suggested_human_review: bool
