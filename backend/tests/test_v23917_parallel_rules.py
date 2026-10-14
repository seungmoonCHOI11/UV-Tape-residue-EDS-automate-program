from app.research import auto_stats

PTS=[
 {'id':'a','human_result':'Residue','features':{'c_roi_global_ratio':3.0,'n_roi_global_ratio':1.5,'o_roi_global_ratio':3.5}},
 {'id':'b','human_result':'Non-residue','features':{'c_roi_global_ratio':3.0,'n_roi_global_ratio':2.8,'o_roi_global_ratio':2.2}},
 {'id':'c','features':{'c_roi_global_ratio':2.2,'n_roi_global_ratio':2.8,'o_roi_global_ratio':3.5}},
]

def test_parallel_auto_rules_do_not_collapse_to_human_labels():
    co=auto_stats(PTS,'co',2.4)
    cno=auto_stats(PTS,'cno',2.4)
    cor=auto_stats(PTS,'c_n_or_o',2.4)
    assert co['residue']==1
    assert cno['residue']==0
    assert cor['residue']==2
    assert co['verified']==2

def test_n_availability_is_reported_separately():
    x=auto_stats(PTS,'cno',2.4)
    assert x['n_available']==3
