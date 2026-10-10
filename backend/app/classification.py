"""Single source of truth for UV-tape residue classification.

Human verification is the final ground truth.  For unverified points the current
C/O ROI-to-global rule is evaluated at read/export time so older saved labels do
not become stale when the engineering threshold changes.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

C_RESIDUE_RATIO = 2.40
O_RESIDUE_RATIO = 3.00
MODERATE_RATIO = 2.00


def element_ratio_score(ratio: float, strong_ratio: float) -> float:
    """Return an element score on the common 0-100 scale."""
    x = max(0.0, float(ratio))
    strong = float(strong_ratio)
    if x < MODERATE_RATIO:
        return max(0.0, min(59.0, (x / MODERATE_RATIO) * 59.0))
    if x < strong:
        return 60.0 + ((x - MODERATE_RATIO) / (strong - MODERATE_RATIO)) * 10.0
    equivalent = x * (3.0 / strong)
    k = 0.23
    denom = 1.0 - math.exp(-k * (20.0 - 3.0))
    normalized = (1.0 - math.exp(-k * (equivalent - 3.0))) / denom if denom else 0.0
    return min(100.0, 70.0 + 30.0 * max(0.0, normalized))


def co_ratio_score(c_ratio: float, o_ratio: float) -> float:
    return min(
        element_ratio_score(c_ratio, C_RESIDUE_RATIO),
        element_ratio_score(o_ratio, O_RESIDUE_RATIO),
    )


def co_rule_result(c_ratio: float, o_ratio: float) -> str:
    c, o = float(c_ratio), float(o_ratio)
    if c >= C_RESIDUE_RATIO and o >= O_RESIDUE_RATIO:
        return "Residue"
    if c >= MODERATE_RATIO and o >= MODERATE_RATIO:
        return "Ambiguous"
    return "Non-residue"


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if math.isfinite(v):
            return v
    return None


def _has_human_roi(features: Dict[str, Any]) -> bool:
    polygons = features.get("human_roi_polygons")
    polygon = features.get("human_roi_polygon")
    return bool(polygons or polygon)


def feature_ratios(features: Optional[Dict[str, Any]], prefer_human: bool = True) -> Tuple[Optional[float], Optional[float], str]:
    """Return (C ratio, O ratio, source)."""
    f = features or {}
    if prefer_human and _has_human_roi(f):
        c = _number(f.get("human_c_ratio"))
        o = _number(f.get("human_o_ratio"))
        if c is not None and o is not None:
            return c, o, "human_roi"

    c = _number(f.get("c_roi_global_ratio"))
    o = _number(f.get("o_roi_global_ratio"))
    if c is None:
        roi, global_ = _number(f.get("c_roi_mean")), _number(f.get("c_global_mean"))
        if roi is not None and global_ not in (None, 0.0):
            c = roi / global_
    if o is None:
        roi, global_ = _number(f.get("o_roi_mean")), _number(f.get("o_global_mean"))
        if roi is not None and global_ not in (None, 0.0):
            o = roi / global_
    return c, o, "auto"


def auto_result(record: Dict[str, Any]) -> str:
    f = record.get("features") or {}
    c, o, _ = feature_ratios(f, prefer_human=True)
    if c is not None and o is not None:
        return co_rule_result(c, o)
    fallback = record.get("cv_result") or record.get("result") or f.get("result") or f.get("human_roi_rule_result")
    return "Ambiguous" if fallback in (None, "Review") else str(fallback)


def record_result(record: Dict[str, Any]) -> str:
    """Human verified label wins; otherwise always use the current C/O rule."""
    human = record.get("human_result")
    if human:
        return str(human)
    return auto_result(record)


def record_score(record: Dict[str, Any]) -> Optional[float]:
    """Current C/O score on 0-100 scale, independent of the final human label."""
    c, o, _ = feature_ratios(record.get("features") or {}, prefer_human=True)
    if c is not None and o is not None:
        return round(co_ratio_score(c, o), 1)
    f = record.get("features") or {}
    raw = f.get("residue_score", record.get("residue_score"))
    raw = _number(raw)
    if raw is None:
        return None
    return round(raw * 100.0 if raw <= 1.000001 else raw, 1)


CLASSIFICATION_RULE_TEXT = (
    "C ROI/Global >= 2.40x AND O ROI/Global >= 3.00x -> Residue; "
    "both >= 2.00x but either residue gate unmet -> Ambiguous; otherwise Non-residue."
)
