"""Run with: python -m unittest discover -s tests -v (from backend)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main


from test_v23747_persistence import FakeDB as PersistenceFakeDB, FakeR2

class FakeDB(PersistenceFakeDB):
    """Updated fixture for the manifest/read-after-write contract in v23.7.47+."""
    def __init__(self):
        super().__init__()
        self.project={"id":"project","description":"{}","status":"Processing"}
    def configured(self): return True
    def get_project(self,pid): return dict(self.project)
    def get_latest_project(self): return self.get_project("project")
    def update_project(self,pid,**values): self.project.update(values)
    def get_ground_truth_examples(self,*a,**kw): return []


class SingleUploadTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeDB();self.uploads=[]
        self.r2=FakeR2()
        self.patches=[patch.object(main,"db",self.db),patch.object(main,"r2",self.r2)]
        for p in self.patches:p.start()
        main.JOBS.clear();main.PROJECTS.clear();main.RECORDS.clear();main.CANCELLED_PROJECTS.clear()
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        main.set_job("job",persist_upload=True,project_id="project",status="queued",completed=0,total=2)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def run_job(self,n=2,fail_second=False):
        def extract(*a,record_callback=None,collect_records=True,point_error_callback=None,structure_callback=None,**kw):
            self.assertFalse(collect_records)
            if structure_callback:
                missing=[{"id":"150W_30s_W1_P2","wafer":1,"point":2}] if n<2 else []
                structure_callback({"expected_points":2,"detected_points":n,"missing_count":len(missing),"missing_points":missing,"recognition_mode":"point_label"})
            saved_before=0
            for i in range(n):
                if i==1 and fail_second:
                    point_error_callback({"id":"150W_30s_W1_P2","wafer":1,"point":2,"stage":"worker","error":"worker interrupted"})
                    continue
                try:
                    asset=self.path/"sem.jpg";asset.write_bytes(b"fixture")
                    record_callback({"id":f"150W_30s_W1_P{i+1}","power":"150W","time":"30s","wafer":1,"point":i+1,"features":{},"assets":{k:str(asset) for k in ("sem","eds_map","c_map","o_map")}})
                    saved_before+=1
                except Exception as exc:
                    point_error_callback({"id":f"150W_30s_W1_P{i+1}","wafer":1,"point":i+1,"stage":"save","error":str(exc)})
            return []
        with patch.object(main,"extract_pdfs",extract):
            main.process_upload_job("job","project",self.path,[],["source.pdf"],{},{},
                [{"power":"150","time":"30","wafers":"1","points":"1-2"}],3,"SiCN",{},"MAIN","CMP",1)
    def test_streamed_save_and_complete(self):
        self.run_job()
        self.assertEqual(main.get_job("job")["status"],"completed")
        self.assertEqual(len(self.db.assets),2)
        self.assertEqual(main.get_job("job")["completed"],2)
    def test_partial_is_not_completed(self):
        self.run_job(n=1)
        self.assertEqual(main.get_job("job")["status"],"partial")
        self.assertEqual(main.get_job("job")["total"],2)
    def test_worker_failure_keeps_saved_point_and_finishes_partial(self):
        self.run_job(fail_second=True)
        job=main.get_job("job")
        self.assertEqual(job["status"],"partial")
        self.assertEqual(job["completed"],1)
        self.assertEqual(job["failed_count"],1)
        self.assertEqual(job["failed_points"][0]["point"],2)
        self.assertEqual(len(self.db.assets),1)
    def test_asset_failure_cannot_count_point_as_saved(self):
        self.r2.upload_file=lambda *a:(_ for _ in ()).throw(RuntimeError("R2 unavailable"))
        self.run_job()
        self.assertEqual(main.get_job("job")["status"],"failed")
        self.assertEqual(main.get_job("job")["completed"],0)
        self.assertFalse(self.db.points)
    def test_restart_reports_interruption_not_completion(self):
        main.set_job("job",status="processing",completed=1)
        main.JOBS.clear()
        state=main.job_status("job","project")
        self.assertEqual(state["status"],"interrupted")
        self.assertEqual(state["completed"],1)
    def test_completed_status_survives_restart(self):
        self.run_job();main.JOBS.clear()
        self.assertEqual(main.job_status("job","project")["status"],"completed")
    def test_upload_rejects_multiple_pdfs(self):
        c=TestClient(main.app)
        r=c.post('/api/upload',files=[('files',('a.pdf',b'a','application/pdf')),('files',('b.pdf',b'b','application/pdf'))],data={'conditions_json':'[]'})
        self.assertEqual(r.status_code,400)
        self.assertFalse(main.ANALYSIS_SLOT.locked())
    def test_concurrent_upload_rejected(self):
        main.ANALYSIS_SLOT.acquire()
        try:
            c=TestClient(main.app)
            r=c.post('/api/upload',files={'files':('a.pdf',b'a','application/pdf')},data={'conditions_json':'[]'})
            self.assertEqual(r.status_code,409)
        finally:main.ANALYSIS_SLOT.release()
    def test_cors_vercel_origin(self):
        c=TestClient(main.app)
        r=c.options('/api/upload',headers={'Origin':'https://eds-test.vercel.app','Access-Control-Request-Method':'POST'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers['access-control-allow-origin'],'https://eds-test.vercel.app')

if __name__=='__main__':unittest.main()
