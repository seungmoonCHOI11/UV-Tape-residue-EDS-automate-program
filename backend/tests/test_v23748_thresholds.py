from app.point_worker import (
    C_RESIDUE_RATIO,
    O_RESIDUE_RATIO,
    element_ratio_score,
    co_rule_result,
)


def score(c, o):
    return min(
        element_ratio_score(c, C_RESIDUE_RATIO),
        element_ratio_score(o, O_RESIDUE_RATIO),
    ) * 100.0


def test_element_specific_residue_gate():
    assert co_rule_result(2.40, 3.00) == "Residue"
    assert co_rule_result(2.72, 7.17) == "Residue"
    assert co_rule_result(2.39, 3.00) == "Ambiguous"
    assert co_rule_result(2.40, 2.99) == "Ambiguous"
    assert co_rule_result(1.99, 10.0) == "Non-residue"


def test_score_boundaries_match_labels():
    assert round(score(2.40, 3.00), 6) == 70.0
    assert 60.0 <= score(2.39, 3.00) < 70.0
    assert score(1.99, 10.0) < 60.0
    assert 70.0 <= score(2.72, 7.17) < 100.0


def test_reported_case_c259_o445_is_residue():
    assert co_rule_result(2.59, 4.45) == "Residue"
    assert score(2.59, 4.45) >= 70.0
