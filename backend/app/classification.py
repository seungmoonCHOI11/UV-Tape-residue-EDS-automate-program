"""Single source of truth for UV-tape residue screening criteria.

Human verification remains the final ground truth. Automatic labels and scores can
be evaluated with one of three screening criteria without rewriting stored data:
- co: C AND O (legacy/current production rule)
- cno: C AND N AND O
- cn: C AND N
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

C_RESIDUE_RATIO = 2.40
O_RESIDUE_RATIO = 3.00
N_RESIDUE_RATIO = 2.40
MODERATE_RATIO = 2.00
VALID_CRITERIA = {"co", "cno", "cn"}


def normalize_criterion(value: Any) -> str:
    key = str(value or "co").strip().lower()
    if key == "c_n_or_o":  # legacy UI id from v23.9.16-v23.9.19
        key = "cn"
    return key if key in VALID_CRITERIA else "co"


def element_ratio_score(ratio: float, strong_ratio: float) -> float:
    """Return an element score on the common 0-100 scale."""
    x = max(0.0, float(ratio))
    strong = max(MODERATE_RATIO + 1e-6, float(strong_ratio))
    if x < MODERATE_RATIO:
        return max(0.0, min(59.0, (x / MODERATE_RATIO) * 59.0))
    if x < strong:
        return 60.0 + ((x - MODERATE_RATIO) / (strong - MODERATE_RATIO)) * 10.0
    equivalent = x * (3.0 / strong)
    k = 0.23
    denom = 1.0 - math.exp(-k * (20.0 - 3.0))
    normalized = (1.0 - math.exp(-k * (equivalent - 3.0))) / denom if denom else 0.0
    return min(100.0, 70.0 + 30.0 * max(0.0, normalized))


def criterion_score(c_ratio: float, n_ratio: Optional[float], o_ratio: Optional[float], criterion: str = "co", n_threshold: float = N_RESIDUE_RATIO, c_threshold: float = C_RESIDUE_RATIO, o_threshold: float = O_RESIDUE_RATIO) -> Optional[float]:
    criterion = normalize_criterion(criterion)
    c_score = element_ratio_score(c_ratio, c_threshold)
    if criterion == "co":
        if o_ratio is None:
            return None
        return min(c_score, element_ratio_score(o_ratio, o_threshold))
    if n_ratio is None:
        return None
    n_score = element_ratio_score(n_ratio, n_threshold)
    if criterion == "cn":
        return min(c_score, n_score)
    if o_ratio is None:
        return None
    return min(c_score, n_score, element_ratio_score(o_ratio, o_threshold))


def criterion_result(c_ratio: float, n_ratio: Optional[float], o_ratio: Optional[float], criterion: str = "co", n_threshold: float = N_RESIDUE_RATIO, c_threshold: float = C_RESIDUE_RATIO, o_threshold: float = O_RESIDUE_RATIO) -> Optional[str]:
    criterion = normalize_criterion(criterion)
    c = float(c_ratio)
    if criterion == "co":
        if o_ratio is None:
            return None
        o = float(o_ratio)
        strong = c >= c_threshold and o >= o_threshold
        moderate = c >= MODERATE_RATIO and o >= MODERATE_RATIO
    else:
        if n_ratio is None:
            return None
        n = float(n_ratio)
        if criterion == "cn":
            strong = c >= c_threshold and n >= n_threshold
            moderate = c >= MODERATE_RATIO and n >= MODERATE_RATIO
        else:
            if o_ratio is None:
                return None
            o = float(o_ratio)
            strong = c >= c_threshold and n >= n_threshold and o >= o_threshold
            moderate = c >= MODERATE_RATIO and n >= MODERATE_RATIO and o >= MODERATE_RATIO
    if strong:
        return "Residue"
    if moderate:
        return "Ambiguous"
    return "Non-residue"


def co_ratio_score(c_ratio: float, o_ratio: float) -> float:
    return float(criterion_score(c_ratio, None, o_ratio, "co") or 0.0)


def co_rule_result(c_ratio: float, o_ratio: float) -> str:
    return str(criterion_result(c_ratio, None, o_ratio, "co") or "Ambiguous")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if math.isfinite(v):
            return v
    return None


def _has_human_roi(features: Dict[str, Any]) -> bool:
    return bool(features.get("human_roi_polygons") or features.get("human_roi_polygon"))


def feature_element_ratios(features: Optional[Dict[str, Any]], prefer_human: bool = True) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """Return (C ratio, N ratio, O ratio, source). Legacy N can be reconstructed from log2 ratio."""
    f = features or {}
    if prefer_human and _has_human_roi(f):
        c = _number(f.get("human_c_ratio"))
        n = _number(f.get("human_n_ratio"))
        o = _number(f.get("human_o_ratio"))
        if c is not None and (n is not None or o is not None):
            return c, n, o, "human_roi"

    def ratio(prefix: str) -> Optional[float]:
        direct = _number(f.get(f"{prefix}_roi_global_ratio"))
        if direct is not None:
            return direct
        roi, global_ = _number(f.get(f"{prefix}_roi_mean")), _number(f.get(f"{prefix}_global_mean"))
        if roi is not None and global_ not in (None, 0.0):
            return roi / global_
        log2v = _number(f.get(f"{prefix}_log2_ratio"))
        if log2v is not None:
            try:
                v = 2.0 ** log2v
                return v if math.isfinite(v) else None
            except OverflowError:
                return None
        return None

    return ratio("c"), ratio("n"), ratio("o"), "auto"


def feature_ratios(features: Optional[Dict[str, Any]], prefer_human: bool = True) -> Tuple[Optional[float], Optional[float], str]:
    """Backward-compatible C/O accessor."""
    c, _n, o, src = feature_element_ratios(features, prefer_human)
    return c, o, src


def auto_result(record: Dict[str, Any], criterion: str = "co", n_threshold: float = N_RESIDUE_RATIO, c_threshold: float = C_RESIDUE_RATIO, o_threshold: float = O_RESIDUE_RATIO) -> str:
    f = record.get("features") or {}
    c, n, o, _ = feature_element_ratios(f, prefer_human=True)
    if c is not None and o is not None:
        result = criterion_result(c, n, o, criterion, n_threshold, c_threshold, o_threshold)
        if result is not None:
            return result
        # Missing N under an N-dependent criterion is a review case, never a false negative.
        if normalize_criterion(criterion) != "co":
            return "Ambiguous"
    fallback = record.get("cv_result") or record.get("result") or f.get("result") or f.get("human_roi_rule_result")
    return "Ambiguous" if fallback in (None, "Review") else str(fallback)


def record_result(record: Dict[str, Any], criterion: str = "co", n_threshold: float = N_RESIDUE_RATIO, c_threshold: float = C_RESIDUE_RATIO, o_threshold: float = O_RESIDUE_RATIO) -> str:
    """Human verified label wins; otherwise evaluate the selected automatic rule."""
    human = record.get("human_result")
    if human:
        return str(human)
    return auto_result(record, criterion, n_threshold, c_threshold, o_threshold)


def record_score(record: Dict[str, Any], criterion: str = "co", n_threshold: float = N_RESIDUE_RATIO, c_threshold: float = C_RESIDUE_RATIO, o_threshold: float = O_RESIDUE_RATIO) -> Optional[float]:
    """Screening score on 0-100 scale for the selected criterion."""
    c, n, o, _ = feature_element_ratios(record.get("features") or {}, prefer_human=True)
    if c is not None and o is not None:
        live = criterion_score(c, n, o, criterion, n_threshold, c_threshold, o_threshold)
        if live is not None:
            return round(live, 1)
        if normalize_criterion(criterion) != "co":
            return None
    f = record.get("features") or {}
    raw = _number(f.get("residue_score", record.get("residue_score")))
    if raw is None:
        return None
    return round(raw * 100.0 if raw <= 1.000001 else raw, 1)


def criterion_rule_text(criterion: str = "co", n_threshold: float = N_RESIDUE_RATIO, c_threshold: float = C_RESIDUE_RATIO, o_threshold: float = O_RESIDUE_RATIO) -> str:
    criterion = normalize_criterion(criterion)
    if criterion == "cno":
        return f"C >= {c_threshold:.2f}x AND N >= {n_threshold:.2f}x AND O >= {o_threshold:.2f}x -> Residue; all three >= {MODERATE_RATIO:.2f}x but a strong gate unmet -> Ambiguous; otherwise Non-residue."
    if criterion == "cn":
        return f"C >= {c_threshold:.2f}x AND N >= {n_threshold:.2f}x -> Residue; both >= {MODERATE_RATIO:.2f}x but a strong gate unmet -> Ambiguous; otherwise Non-residue."
    return f"C >= {c_threshold:.2f}x AND O >= {o_threshold:.2f}x -> Residue; both >= {MODERATE_RATIO:.2f}x but either strong gate unmet -> Ambiguous; otherwise Non-residue."


CLASSIFICATION_RULE_TEXT = criterion_rule_text("co")
