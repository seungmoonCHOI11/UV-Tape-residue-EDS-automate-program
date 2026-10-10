"""Bounded report queue. Does not write points, labels or original assets."""
import json,uuid,threading,queue,subprocess,sys,os,time,shutil
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse
from .research import ExportOptions,select,page_count,summary

def install_report_routes(app,loader,analysis_slot,root):
    root=Path(root)/'_research_reports';root.mkdir(parents=True,exist_ok=True)
    jobs={};lock=threading.Lock();pending=queue.Queue();max_jobs=3
    def update(jid,**data):
        with lock:jobs[jid].update(data)
    def cleanup():
        for path in root.iterdir():
            if path.is_dir() and len(path.name)==32 and time.time()-path.stat().st_mtime>86400 and (path.name not in jobs or jobs[path.name].get('status') in ('completed','failed')):shutil.rmtree(path,ignore_errors=True)
    def worker():
        while True:
            jid=pending.get();folder=root/jid
            try:
                update(jid,message='Waiting for analysis to finish',status='queued')
                # Shared with upload/reanalysis. Never render a report beside a heavy analysis.
                with analysis_slot:
                    update(jid,status='running',message='Preparing report',progress=0)
                    with (folder/'worker.log').open('w') as log:
                        proc=subprocess.Popen([sys.executable,'-m','app.report_worker',str(folder)],cwd=str(Path(__file__).resolve().parents[1]),stdout=log,stderr=log,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
                        try:code=proc.wait(timeout=7200)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait();raise RuntimeError('Report exceeded 2 hours; select fewer points.')
                    if code:raise RuntimeError('Report generation failed. Check backend report worker log.')
                    result=json.loads((folder/'result.json').read_text());update(jid,status='completed',progress=100,message='Ready to download',**result)
            except Exception as exc:
                print(f'[research-report] {jid}: {exc}');update(jid,status='failed',message=str(exc))
            finally:
                (folder/'request.json').unlink(missing_ok=True)
                shutil.rmtree(folder/'assets',ignore_errors=True)
                pending.task_done()
    threading.Thread(target=worker,daemon=True,name='research-report-queue').start()
    @app.post('/api/research/reports')
    def create_report(opts:ExportOptions):
        if opts.kind=='summary' and (opts.cohort.results is not None or opts.cohort.score_bands is not None):
            raise HTTPException(422,'Summary rates require all result classes and scores; clear result/score filters.')
        cleanup()
        with lock:
            if sum(j['status'] in ('queued','running','preparing') for j in jobs.values())>=max_jobs:raise HTTPException(429,'Report queue is full. Wait for an existing report.')
            jid=uuid.uuid4().hex;jobs[jid]={'id':jid,'status':'preparing','progress':0,'message':'Selecting snapshot','created_at':time.time()}
        folder=root/jid;folder.mkdir()
        try:
            records=select(loader(),opts.cohort)
            if not records:raise HTTPException(422,'No points match the selected filters.')
            # Includes only selected persisted IDs. Snapshot locks labels and scores for this output.
            opts.cohort.point_ids=[str(p['id']) for p in records]
            (folder/'request.json').write_text(json.dumps({'records':records,'options':opts.model_dump()},ensure_ascii=False,default=str),encoding='utf8')
            update(jid,status='queued',message='Queued',n=len(records),pages=page_count(records,opts),kind=opts.kind,format=opts.format)
            pending.put(jid);return dict(jobs[jid])
        except HTTPException:
            update(jid,status='failed',message='No matching points');raise
        except Exception as exc:
            update(jid,status='failed',message='Cannot read saved data');raise HTTPException(503,'Saved dataset could not be read; try again.') from exc
    @app.get('/api/research/reports/{jid}')
    def report_status(jid:str):
        if len(jid)!=32 or any(c not in '0123456789abcdef' for c in jid):raise HTTPException(404,'Unknown report')
        with lock:state=dict(jobs.get(jid) or {})
        folder=root/jid
        if not state:
            if (folder/'result.json').exists():return {'id':jid,'status':'completed','progress':100,**json.loads((folder/'result.json').read_text())}
            if folder.exists():return {'id':jid,'status':'interrupted','message':'Server restarted. Create the report again.'}
            raise HTTPException(404,'Report expired or server storage reset. Create it again.')
        if state['status']=='running':
            try:state.update(json.loads((folder/'progress.json').read_text()))
            except (OSError,ValueError):pass
        return state
    @app.get('/api/research/reports/{jid}/download')
    def download(jid:str):
        state=report_status(jid)
        if state['status']!='completed':raise HTTPException(409,'Report is not complete.')
        file=root/jid/'report_bundle.zip'
        if not file.exists():raise HTTPException(410,'Report expired. Create it again.')
        return FileResponse(file,filename=f'EDS_{jid[:8]}_report.zip',media_type='application/zip')
