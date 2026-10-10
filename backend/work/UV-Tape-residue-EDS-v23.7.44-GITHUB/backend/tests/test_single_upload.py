"""Run with: python -m unittest discover -s tests -v (from backend)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main


class FakeDB:
    def __init__(self):
        self.project={"id":"project","description":"{}","status":"Processing"}
        self.points=[]; self.assets=[]
    def configured(self): return True
    def get_project(self,pid): return dict(self.project)
    def get_latest_project(self): return self.get_project("project")
    def update_project(self,pid,**values): self.project.update(values)
    def get_ground_truth_examples(self,*a,**kw): return []
    def upsert_point(self,pid,r):
        self.points.append(r["id"])
        return {"id":"db-"+r["id"]}
    def upsert_analysis(self,*a): pass
    def upsert_asset(self,*a): self.assets.append(a)


class SingleUploadTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeDB();self.uploads=[]
        self.r2=SimpleNamespace(configured=True,upload_file=lambda *a:self.uploads.append(a))
        self.patches=[patch.object(main,"db",self.db),patch.object(main,"r2",self.r2)]
        for p in self.patches:p.start()
        main.JOBS.clear();main.PROJECTS.clear();main.RECORDS.clear();main.CANCELLED_PROJECTS.clear()
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        main.set_job("job",persist_upload=True,project_id="project",status="queued",completed=0,total=2)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def run_job(self,n=2,fail_second=False):
        def extract(*a,record_callback=None,collect_records=True,**kw):
            self.assertFalse(collect_records)
            for i in range(n):
                self.assertEqual(len(self.db.points),i) # saved before next point analysis
                if i==1 and fail_second:raise RuntimeError("worker interrupted")
                record_callback({"id":f"p{i}","features":{},"assets":{"sem":self.path/"sem.jpg"}})
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
    def test_worker_failure_keeps_saved_point(self):
        self.run_job(fail_second=True)
        self.assertEqual(main.get_job("job")["status"],"failed")
        self.assertEqual(main.get_job("job")["completed"],1)
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
