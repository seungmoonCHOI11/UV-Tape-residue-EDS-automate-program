"""Point-level resilience tests for v23.7.45."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app import analysis


class PointFailureContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.groups=[
            {"point_number":i,"pages":[("fake.pdf",(i-1)*3+j) for j in range(3)]}
            for i in (1,2,3)
        ]
        self.conditions=[{"power":"150","time":"30","wafers":"1","points":"1-3"}]

    def tearDown(self):
        self.tmp.cleanup()

    def _success_result(self,payload):
        Path(payload["result_path"]).write_text(json.dumps({
            "id":payload["id"],"power":payload["power"],"time":payload["time"],
            "wafer":payload["wafer"],"point":payload["point"],"zone":payload["zone"],
            "features":{},"assets":{}
        }))

    def test_worker_failure_skips_only_bad_point(self):
        saved=[];failures=[];structures=[]
        def fake_run(cmd,**kwargs):
            payload=json.loads(Path(cmd[-1]).read_text())
            if payload["point"]==2:
                return SimpleNamespace(returncode=1,stderr="simulated worker crash",stdout="")
            self._success_result(payload)
            return SimpleNamespace(returncode=0,stderr="",stdout="")
        with patch.object(analysis,"_build_point_groups",return_value=self.groups), patch.object(analysis.subprocess,"run",side_effect=fake_run):
            analysis.extract_pdfs(["fake.pdf"],self.root,self.conditions,
                record_callback=lambda r:saved.append(r["point"]),collect_records=False,
                point_error_callback=failures.append,structure_callback=structures.append)
        self.assertEqual(saved,[1,3])
        self.assertEqual(failures[0]["point"],2)
        self.assertEqual(failures[0]["stage"],"worker")
        self.assertEqual(structures[0]["detected_points"],3)

    def test_save_failure_skips_only_bad_point(self):
        saved=[];failures=[]
        def fake_run(cmd,**kwargs):
            payload=json.loads(Path(cmd[-1]).read_text())
            self._success_result(payload)
            return SimpleNamespace(returncode=0,stderr="",stdout="")
        def save(record):
            if record["point"]==2:
                raise RuntimeError("simulated R2 save failure")
            saved.append(record["point"])
        with patch.object(analysis,"_build_point_groups",return_value=self.groups), patch.object(analysis.subprocess,"run",side_effect=fake_run):
            analysis.extract_pdfs(["fake.pdf"],self.root,self.conditions,
                record_callback=save,collect_records=False,point_error_callback=failures.append)
        self.assertEqual(saved,[1,3])
        self.assertEqual(failures[0]["point"],2)
        self.assertEqual(failures[0]["stage"],"save")


if __name__=="__main__":
    unittest.main()
