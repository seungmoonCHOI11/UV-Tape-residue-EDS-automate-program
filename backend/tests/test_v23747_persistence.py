import tempfile
from pathlib import Path

from app.persistence import build_manifest, persist_point, reconcile_project


class FakeR2:
    configured=True
    def __init__(self, fail_once=False):
        self.keys=set(); self.fail_once=fail_once; self.calls=0
    def upload_file(self, local_path, key, content_type=None):
        self.calls += 1
        if self.fail_once:
            self.fail_once=False
            raise OSError(11, "Resource temporarily unavailable")
        self.keys.add(key); return key
    def list_keys(self, prefix):
        return sorted(k for k in self.keys if k.startswith(prefix))


class FakeDB:
    def __init__(self):
        self.points={}; self.analyses={}; self.assets={}; self.n=0
    def upsert_point(self, project_id, r):
        key=(project_id,int(str(r['power']).rstrip('W')),int(str(r['time']).rstrip('s')),int(r['wafer']),int(r['point']))
        if key not in self.points:
            self.n+=1
            self.points[key]={"id":f"db{self.n}","project_id":project_id,"power":key[1],"time_sec":key[2],"wafer":key[3],"point":key[4],"position":r.get('zone')}
        return self.points[key]
    def upsert_analysis(self, point_id, features):
        self.analyses[point_id]={"point_id":point_id,"features":dict(features or {})}
    def upsert_asset(self, point_id, asset_type, storage_path):
        self.assets.setdefault(point_id,{})[asset_type]=storage_path
    def get_point_by_key(self, project_id,power,time_sec,wafer,point):
        return self.points.get((project_id,power,time_sec,wafer,point))
    def get_analysis(self, point_id):
        return self.analyses.get(point_id)
    def get_assets(self, point_id):
        return [{"asset_type":k,"storage_path":v} for k,v in self.assets.get(point_id,{}).items()]
    def get_points_with_data(self, project_id):
        out=[]
        for key,row in self.points.items():
            if key[0]!=project_id: continue
            pid=row['id']
            out.append((row,self.analyses.get(pid),dict(self.assets.get(pid,{}))))
        return out


def make_record(tmp, point):
    assets={}
    for name in ("sem","eds_map","c_map","o_map"):
        f=tmp/f"{point}_{name}.jpg"; f.write_bytes(b"x"); assets[name]=str(f)
    return {
        "id":f"250W_30s_W1_P{point}","source_point_id":f"250W_30s_W1_P{point}",
        "power":"250W","time":"30s","wafer":1,"point":point,"zone":"Center",
        "features":{"result":"Residue"},"assets":assets,
    }


def test_transient_save_error_retries_same_point_without_shift():
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td); db=FakeDB(); r2=FakeR2(fail_once=True)
        r=make_record(tmp,2)
        result=persist_point("p",r,db,r2,attempts=3,backoff=(0,0,0),sleep_fn=lambda _:None)
        assert result["source_id"]=="250W_30s_W1_P2"
        assert result["attempt"]==2
        assert db.get_point_by_key("p",250,30,1,2) is not None
        assert db.get_point_by_key("p",250,30,1,3) is None


def test_reconciliation_reports_exact_missing_last_point():
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td); db=FakeDB(); r2=FakeR2()
        manifest=build_manifest([
            {"power":250,"time":30,"wafer":1,"point":1},
            {"power":250,"time":30,"wafer":1,"point":2},
            {"power":250,"time":30,"wafer":5,"point":9},
        ])
        expected={}
        for point in (1,2):
            r=make_record(tmp,point)
            persist_point("p",r,db,r2,attempts=1,backoff=(),sleep_fn=lambda _:None)
            expected[r["source_point_id"]]=set(r["assets"])
        rec=reconcile_project("p",manifest,db,r2,expected_assets_by_id=expected)
        assert rec["complete_count"]==2
        assert rec["incomplete_count"]==1
        assert rec["incomplete_ids"]==["250W_30s_W5_P9"]
        assert rec["incomplete"][0]["missing"]==["point_row"]


def test_manifest_slots_are_stable():
    manifest=build_manifest([
        {"power":250,"time":30,"wafer":1,"point":1},
        {"power":250,"time":30,"wafer":1,"point":2},
        {"power":250,"time":30,"wafer":1,"point":3},
    ])
    assert [x["sequence_index"] for x in manifest]==[1,2,3]
    assert [x["id"] for x in manifest]==["250W_30s_W1_P1","250W_30s_W1_P2","250W_30s_W1_P3"]


def test_image_only_group_count_mismatch_stops_before_shift(tmp_path, monkeypatch):
    from app import analysis
    groups=[
        {"point_number":None,"pages":[("fake.pdf",i*3+j) for j in range(3)]}
        for i in range(2)
    ]
    monkeypatch.setattr(analysis,"_build_point_groups",lambda *a,**k:groups)
    structures=[]
    try:
        analysis.extract_pdfs(
            ["fake.pdf"],tmp_path,[{"power":"250","time":"30","wafers":"1","points":"1-3"}],
            structure_callback=structures.append,
        )
    except ValueError as exc:
        assert "one-Point shift" in str(exc)
    else:
        raise AssertionError("unsafe page-order mismatch should stop")
    assert structures[0]["mapping_safe"] is False
    assert structures[0]["detected_points"]==2
