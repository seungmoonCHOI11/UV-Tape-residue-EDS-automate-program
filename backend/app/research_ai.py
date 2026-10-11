"""Short HTTP requests + bounded background interpretation; no image or label mutation."""
import json,os,threading,uuid,time
from datetime import datetime,timezone
from fastapi import HTTPException
from .research import Cohort,select,summary,stats

class InterpretationError(Exception):
    def __init__(self,code,message):self.code,self.message=code,message;super().__init__(message)

def configuration():
    model=os.getenv('OPENAI_ANALYSIS_MODEL') or os.getenv('OPENAI_MODEL') or ''
    return {'configured':bool(os.getenv('OPENAI_API_KEY') and model),'key_present':bool(os.getenv('OPENAI_API_KEY')),'model':model}

def explain_error(e):
    code=getattr(e,'status_code',None)
    if code==401:return InterpretationError('authentication','OpenAI API 키 인증에 실패했습니다. Render의 OPENAI_API_KEY를 확인하세요.')
    if code==403:return InterpretationError('permission','설정된 모델에 대한 API 사용 권한을 확인하세요.')
    if code==404:return InterpretationError('model','설정된 모델을 사용할 수 없습니다. OPENAI_ANALYSIS_MODEL 또는 OPENAI_MODEL을 확인하세요.')
    if code==429:return InterpretationError('quota','OpenAI API 사용 한도 또는 요청 한도에 도달했습니다. API 결제·한도를 확인하세요.')
    if 'timeout' in type(e).__name__.lower():return InterpretationError('timeout','AI 응답 시간이 초과되었습니다. 잠시 후 다시 요청하세요.')
    if 'connection' in type(e).__name__.lower():return InterpretationError('connection','백엔드에서 OpenAI API에 연결하지 못했습니다. 서버 네트워크를 확인하세요.')
    return InterpretationError('provider','AI 해석에 실패했습니다. 모델 설정과 서버 로그의 오류 종류를 확인하세요.')

def interpret(records,cohort,client_factory=None):
    conf=configuration()
    if not conf['key_present']:raise InterpretationError('configuration','Render에 OPENAI_API_KEY가 설정되어 있지 않습니다.')
    if not conf['model']:raise InterpretationError('configuration','Render에 사용 가능한 OPENAI_ANALYSIS_MODEL 또는 OPENAI_MODEL을 설정하세요.')
    if not records:raise InterpretationError('empty','선택 범위에 해석할 데이터가 없습니다.')
    data=summary(records,cohort)
    if sum(len(s['conditions']) for s in data['strata'])>300:raise InterpretationError('size','조건이 너무 많습니다. 선택 범위를 줄이세요.')
    instructions='''SEM/EDS 연구 결과를 한국어로 해석하세요. 입력은 이미 집계된 스크리닝 판정입니다. Point를 재분류하거나 숫자를 만들지 마세요. 기판을 섞지 말고 W1 Corner/W4 Edge/W5 Middle과 W9 Reference를 구분하세요. Residue는 UV tape 유래의 화학적 확정이 아닙니다. 비율은 Point 수 기준이며 면적률, PRE, 수율이 아닙니다. Ambiguous는 분모에 포함됩니다. 동일 W/P는 짝지은 동일 시료를 보장하지 않습니다. 관찰과 추정을 구분하고 누락/표본 구성의 영향을 밝혀 주세요. JSON 객체로 summary 문자열, key_findings/condition_trends/anomalies/next_steps/caveats 문자열 배열을 반환하세요.'''
    if client_factory is None:
        from openai import OpenAI
        client_factory=OpenAI
    try:
        client=client_factory(timeout=45.0,max_retries=0)
        response=client.responses.create(model=conf['model'],instructions=instructions,input=json.dumps(data,ensure_ascii=False),text={'format':{'type':'json_object'}},max_output_tokens=3000)
        result=json.loads(response.output_text)
        if not isinstance(result,dict) or not isinstance(result.get('summary'),str) or not result['summary'].strip():raise ValueError('Empty summary')
        for key in ['key_findings','condition_trends','anomalies','next_steps','caveats']:
            if not isinstance(result.get(key),list) or not all(isinstance(x,str) for x in result[key]):raise ValueError('Invalid response schema')
        return {k:result[k] for k in ['summary','key_findings','condition_trends','anomalies','next_steps','caveats']}|{'status':'completed','source':{'n':len(records),'cohort':cohort.model_dump(),'created_at':datetime.now(timezone.utc).isoformat(),'model':conf['model']}}
    except (ValueError,TypeError,AttributeError) as e:raise InterpretationError('response','AI가 완성된 해석 형식을 반환하지 않았습니다. 다시 요청하세요.') from e
    except Exception as e:
        print('[research-ai]',type(e).__name__,getattr(e,'status_code',None))
        raise explain_error(e) from e

def install_ai_routes(app,loader,runner=interpret):
    jobs={};lock=threading.Lock()
    @app.get('/api/research/ai/config')
    def config():return configuration()
    @app.post('/api/research/ai')
    def start(cohort:Cohort):
        if not configuration()['configured']:raise HTTPException(503,{'code':'configuration','message':'Render의 OPENAI_API_KEY 및 OPENAI_ANALYSIS_MODEL / OPENAI_MODEL 설정을 확인하세요.'})
        with lock:
            for k in list(jobs):
                if jobs[k]['status']!='running' and time.time()-jobs[k]['created']>3600:del jobs[k]
            if any(j['status']=='running' for j in jobs.values()):raise HTTPException(409,'AI 해석이 이미 진행 중입니다. 완료 후 다시 요청하세요.')
            jid=uuid.uuid4().hex;jobs[jid]={'id':jid,'status':'running','created':time.time()}
        def run():
            try:
                records=select(loader(),cohort);result=runner(records,cohort)
                with lock:jobs[jid].update(status='completed',result=result)
            except InterpretationError as e:
                with lock:jobs[jid].update(status='failed',code=e.code,message=e.message)
            except Exception as e:
                print('[research-ai-job]',type(e).__name__)
                with lock:jobs[jid].update(status='failed',code='dataset',message='저장 데이터를 읽거나 해석 작업을 준비하지 못했습니다.')
        threading.Thread(target=run,daemon=True).start()
        return {'id':jid,'status':'running'}
    @app.get('/api/research/ai/{job_id}')
    def status(job_id:str):
        with lock:result=dict(jobs.get(job_id) or {})
        if not result:raise HTTPException(404,'해석 작업이 만료되었거나 서버가 재시작되었습니다. 다시 요청하세요.')
        return result
