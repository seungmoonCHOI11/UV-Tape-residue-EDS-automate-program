from app.classification import criterion_result, criterion_score, feature_element_ratios, record_result


def test_cno_requires_all_three_strong():
    assert criterion_result(2.5, 2.5, 3.2, 'cno', 2.4) == 'Residue'
    assert criterion_result(2.5, 2.2, 3.2, 'cno', 2.4) == 'Ambiguous'
    assert criterion_result(2.5, 1.8, 3.2, 'cno', 2.4) == 'Non-residue'


def test_cn_requires_c_and_n():
    assert criterion_result(2.5, 2.5, 1.5, 'cn', 2.4) == 'Residue'
    assert criterion_result(2.5, 2.2, 4.0, 'cn', 2.4) == 'Ambiguous'
    assert criterion_result(1.8, 3.0, 4.0, 'cn', 2.4) == 'Non-residue'


def test_score_matches_boolean_structure():
    cno = criterion_score(2.5, 2.1, 3.5, 'cno', 2.4)
    flexible = criterion_score(2.5, 2.5, 1.5, 'cn', 2.4)
    assert cno is not None and cno < 70
    assert flexible is not None and flexible >= 70


def test_legacy_n_log2_ratio_and_human_override():
    record = {
        'human_result': 'Non-residue',
        'features': {'c_roi_global_ratio': 3.0, 'o_roi_global_ratio': 4.0, 'n_log2_ratio': 1.3219280948873624},
    }
    c, n, o, src = feature_element_ratios(record['features'])
    assert round(n, 3) == 2.5
    assert record_result(record, 'cno', 2.4) == 'Non-residue'

def test_c_and_o_thresholds_are_adjustable():
    assert criterion_result(2.55, 2.55, 2.85, 'co', 2.4, 2.4, 3.0) == 'Ambiguous'
    assert criterion_result(2.55, 2.55, 2.85, 'co', 2.4, 2.5, 2.8) == 'Residue'
    assert criterion_result(2.55, 2.55, 2.85, 'cno', 2.5, 2.5, 2.8) == 'Residue'
