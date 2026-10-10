"""Isolated report process; exits to return image/PPT memory to the OS."""
import json,sys,os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from .research import ExportOptions
from .research_reports import render_export
from .r2_storage import r2

def run(folder):
    folder=Path(folder);payload=json.loads((folder/'request.json').read_text(encoding='utf8'))
    def status(**values):
        p=folder/'progress.json';t=folder/'progress.tmp';t.write_text(json.dumps(values,ensure_ascii=False),encoding='utf8');t.replace(p)
    result=render_export(payload['records'],ExportOptions.model_validate(payload['options']),folder,r2,status)
    (folder/'result.json').write_text(json.dumps(result),encoding='utf8')
if __name__=='__main__':run(sys.argv[1])
