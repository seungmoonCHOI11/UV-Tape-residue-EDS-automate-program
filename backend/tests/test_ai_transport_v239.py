"""Real HTTP exception translation without any outgoing provider request."""
from types import SimpleNamespace
import pytest
from app.research_ai import interpret, InterpretationError
from app.research import Cohort

@pytest.mark.parametrize('exception,code',[(type('APITimeoutError',(Exception,),{}),'timeout'),(type('APIConnectionError',(Exception,),{}),'connection')])
def test_provider_transport_failure_is_explained(monkeypatch,exception,code):
    monkeypatch.setenv('OPENAI_API_KEY','mock')
    monkeypatch.setenv('OPENAI_ANALYSIS_MODEL','mock-model')
    def create(**kwargs):raise exception('mock transport failure')
    def client(**kwargs):return SimpleNamespace(responses=SimpleNamespace(create=create))
    p={'id':'p1','wafer':1,'point':1,'features':{'result':'Residue'}}
    with pytest.raises(InterpretationError) as caught:interpret([p],Cohort(),client)
    assert caught.value.code==code
