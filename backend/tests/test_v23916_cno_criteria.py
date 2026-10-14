from app.classification import criterion_result, criterion_score, feature_element_ratios, record_result


def test_cno_requires_all_three_strong():
    assert criterion_result(2.5, 2.5, 3.2, 'cno', 2.4) == 'Residue'
    assert criterion_result(2.5, 2.2, 3.2, 'cno', 2.4) == 'Ambiguous'
    assert criterion_result(2.5, 1.8, 3.2, 'cno', 2.4) == 'Non-residue'


def test_c_n_or_o_keeps_c_mandatory():
    assert criterion_result(2.5, 2.5, 1.5, 'c_n_or_o', 2.4) == 'Residue'
    assert criterion_result(2.5, 1.5, 3.2, 'c_n_or_o', 2.4) == 'Residue'
    assert criterion_result(2.3, 3.0, 4.0, 'c_n_or_o', 2.4) == 'Ambiguous'
    assert criterion_result(1.8, 3.0, 4.0, 'c_n_or_o', 2.4) == 'Non-residue'


def test_score_matches_boolean_structure():
    cno = criterion_score(2.5, 2.1, 3.5, 'cno', 2.4)
    flexible = criterion_score(2.5, 2.1, 3.5, 'c_n_or_o', 2.4)
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
