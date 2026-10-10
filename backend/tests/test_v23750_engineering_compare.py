from pathlib import Path

from app.classification import record_result, record_score
from app.reports import engineering_summary, export_engineering_ppt, export_engineering_pdf


def rec(power="150W", time="30s", wafer=1, point=1, c=2.59, o=4.45, human=None):
    return {
        "id": f"{power}-{time}-{wafer}-{point}",
        "power": power,
        "time": time,
        "wafer": wafer,
        "point": point,
        "zone": "Unknown",
        "human_result": human,
        "features": {
            "c_roi_global_ratio": c,
            "o_roi_global_ratio": o,
            "score_calibrated": True,
        },
        "assets": {},
    }


def test_current_rule_applies_to_all_auto_results():
    assert record_result(rec(c=2.59, o=4.45)) == "Residue"
    assert record_score(rec(c=2.59, o=4.45)) >= 70
    assert record_result(rec(c=2.30, o=4.45)) == "Ambiguous"
    assert record_result(rec(c=1.90, o=9.00)) == "Non-residue"


def test_human_verified_result_has_priority():
    assert record_result(rec(c=3.5, o=6.0, human="Non-residue")) == "Non-residue"


def test_engineering_summary_focuses_w1_w4_w5_and_spread():
    records=[]
    # W1: 3/3 residue, W4: 0/3, W5: 1/3 -> overall 4/9 and spread 100%p.
    for p in range(1,4): records.append(rec(wafer=1, point=p, c=3.0, o=4.0))
    for p in range(1,4): records.append(rec(wafer=4, point=p, c=1.0, o=1.0))
    records += [rec(wafer=5, point=1, c=3.0, o=4.0), rec(wafer=5, point=2, c=1.0, o=1.0), rec(wafer=5, point=3, c=1.0, o=1.0)]
    records.append(rec(wafer=9, point=1, c=3.0, o=4.0))  # excluded from focus
    row=engineering_summary(records)[0]
    assert row["points"] == 9
    assert row["residue"] == 4
    assert row["excluded_other_wafers"] == 1
    assert row["location_spread"] == 100.0
    assert row["worst_location"] == "Corner"


def test_summary_exports_smoke(tmp_path: Path):
    records=[rec(wafer=w,point=p,c=(3.0 if p%2 else 1.0),o=(4.0 if p%2 else 1.0)) for w in (1,4,5) for p in range(1,10)]
    ppt=tmp_path/"summary.pptx";pdf=tmp_path/"summary.pdf"
    export_engineering_ppt(records,ppt);export_engineering_pdf(records,pdf)
    assert ppt.exists() and ppt.stat().st_size > 10000
    assert pdf.exists() and pdf.stat().st_size > 1000
