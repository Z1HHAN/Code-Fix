import json
from datetime import datetime, timezone
import httpx
import pytest
from app.connection_checks import discover_models, check_execution, test_model as call_test
from app.remote_execution import PROTOCOL
from app.docker_runner import IsolationBlocked
from app.personal import configure_runtime
from app.role_config import RoleConfig
from app.model_profiles import ModelRouter
from scripts.prepare_desktop import prepare_backend

@pytest.fixture
def value(monkeypatch):
    monkeypatch.setenv('CODE_FIX_PERSONAL_MODE', '1')
    monkeypatch.setenv('CODE_FIX_PERSONAL_BUDGET', 'usage')
    monkeypatch.setenv('CODE_FIX_PERSONAL_BASE_URL', 'https://models.example/v1')
    return dict(base_url='https://models.example/v1',api_key='secret-test-only',model='chosen',
                max_output_tokens=2048,token_parameter='max_tokens',price_version='test',approved=True)

def test_metadata_only_get_no_generation(value):
    seen=[]
    def handle(req):
        seen.append(req)
        return httpx.Response(200,json={'data':[{'id':'second'},{'id':'chosen'},{'id':'chosen'}]})
    result=discover_models(value,transport=httpx.MockTransport(handle))
    assert result['ready'] and result['models']==['chosen','second']
    assert len(seen)==1 and seen[0].method=='GET' and seen[0].url.path=='/v1/models'

@pytest.mark.parametrize('status,ready',[(401,False),(403,False),(404,True),(405,True),(429,False),(501,True)])
def test_metadata_status_has_action(value,status,ready):
    result=discover_models(value,transport=httpx.MockTransport(lambda r:httpx.Response(status)))
    assert result['ready'] is ready and result['checks'][0]['action']

class Runner:
    def __init__(self,ok=True,error=None):self.ok,self.error,self.probed=ok,error,False
    def request(self,*args,**kwargs):
        if self.error:raise IsolationBlocked(self.error)
        return {'protocol':PROTOCOL,'status':'available'}
    def preflight(self,folder):
        self.probed=True
        return {'isolation_ready':self.ok,'image':'sha256:'+'a'*64,'reason':'mount permission'}

@pytest.mark.parametrize('ok',[True,False])
def test_actual_probe_required_for_ready(value,tmp_path,ok):
    runner=Runner(ok)
    result=check_execution(value,tmp_path,runner=runner)
    assert runner.probed and result['ready'] is ok and result['isolation_verified'] is ok
    assert result['checks'][-1]['status']==('pass' if ok else 'blocked')

def test_invalid_execution_code_does_not_probe(value,tmp_path):
    runner=Runner(error='远程执行服务返回HTTP 401')
    result=check_execution(value,tmp_path,runner=runner)
    assert not result['ready'] and not runner.probed
    assert '访问码' in result['checks'][0]['detail']

def test_explicit_model_test_independent_of_execution(value,tmp_path):
    seen=[]
    def handle(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2}})
    result=call_test(value,tmp_path,transport=httpx.MockTransport(handle))
    assert result['ready'] and result['model_requests']==1
    assert len(seen)==1 and seen[0]['model']=='chosen' and seen[0]['max_tokens']==128
    assert result['cost']['amount_usd'] is None
    evidence=list((tmp_path/'connection-tests').glob('*/model-call.json'))
    assert len(evidence)==1 and value['api_key'] not in evidence[0].read_text()

def test_model_test_requires_consent_and_unknown_usage_blocks_retry(value,tmp_path):
    count=[]
    def handle(req):
        count.append(req)
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}]})
    transport=httpx.MockTransport(handle)
    assert call_test({**value,'approved':False},tmp_path,transport=transport)['model_requests']==0
    first=call_test(value,tmp_path,transport=transport)
    second=call_test(value,tmp_path,transport=transport)
    assert not first['ready'] and not second['ready'] and len(count)==1 and second['model_requests']==0

def test_model_401_is_actionable_not_isolation_failure(value,tmp_path):
    result=call_test(value,tmp_path,transport=httpx.MockTransport(lambda r:httpx.Response(401)))
    assert not result['ready'] and 'API Key' in result['checks'][0]['detail']
    assert result['model_requests']==1

def test_multiple_models_are_available_for_role_binding(value,tmp_path,monkeypatch):
    monkeypatch.setenv('CODE_FIX_PERSONAL_KEY',value['api_key'])
    monkeypatch.setenv('CODE_FIX_PERSONAL_APPROVED','1')
    monkeypatch.setenv('CODE_FIX_LIVE_APPROVED','1')
    root=tmp_path/'bundle';prepare_backend(root)
    configure_runtime(root,{**value,'models':['chosen','second','third']})
    config=RoleConfig(root)
    settings,_=ModelRouter.resolve(config)
    assert {s.model for s in settings.values()}=={'chosen','second','third'}
    assert settings['primary'].model=='chosen'
    profiles=json.loads((root/'configs/model_profiles.json').read_text())['profiles']
    assert len(profiles)==3

def test_startup_checks_show_unknown_usage_before_task_creation(value,tmp_path):
    from app.connection_checks import check_ledger
    assert check_ledger(tmp_path)['status']=='pass'
    call_test(value,tmp_path,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
        'choices':[{'message':{'content':'OK'}}]})))
    result=check_ledger(tmp_path)
    assert result['status']=='blocked' and '用量未知' in result['detail']

@pytest.mark.parametrize('port,allowed',[(8443,True),(3000,True),(443,True),(22,False),(445,False),(0,False)])
def test_personal_web_ports_match_desktop_validation(monkeypatch,port,allowed):
    from app.providers import validate_url
    url=f'https://models.example:{port}/v1'
    monkeypatch.setenv('CODE_FIX_PERSONAL_MODE','1');monkeypatch.setenv('CODE_FIX_PERSONAL_BASE_URL',url)
    assert validate_url('personal',url) is allowed
    assert not validate_url('siliconflow',f'https://api.siliconflow.cn:{port}/v1') if port!=443 else True

def test_desktop_excludes_only_cli_and_mock_chain(tmp_path):
    root=tmp_path/'bundle';prepare_backend(root)
    for name in ('cli','model_client','mock_workflow','model_context','quixbugs_import'):
        assert not (root/'app'/f'{name}.py').exists()
    for name in ('live_model','model_profiles','api','service','workflow_v3','connection_checks'):
        assert (root/'app'/f'{name}.py').is_file()
    manifest=json.loads((root/'desktop-build.json').read_text())['files']
    assert 'app/mock_workflow.py' not in manifest

@pytest.mark.parametrize('error,message',[
    (httpx.ReadTimeout('test'),'超时'),
    (httpx.ConnectError('test'),'无法建立'),
])
def test_remote_errors_do_not_expose_endpoint_or_credentials(error,message):
    from app.remote_runner import RemoteRunner
    client=httpx.Client(base_url='https://execution.example',transport=httpx.MockTransport(lambda r:(_ for _ in ()).throw(error)))
    runner=RemoteRunner(url='https://execution.example',token='x'*40,client=client)
    with pytest.raises(IsolationBlocked,match=message) as caught:
        runner.request('GET','/v1/health')
    assert 'execution.example' not in str(caught.value) and 'x'*40 not in str(caught.value)
    client.close()

@pytest.mark.parametrize('age,verified',[(0,True),(600,False)])
def test_recent_probe_in_readiness_expires(monkeypatch,age,verified):
    import importlib
    from datetime import timedelta
    module=importlib.import_module('app.readiness')
    class Config:
        def __init__(self,*args):pass
        def preflight(self):pass
    monkeypatch.setattr(module,'RoleConfig',Config)
    monkeypatch.setattr(module.ModelRouter,'resolve',lambda c:({},None))
    monkeypatch.setenv('CODE_FIX_PERSONAL_MODE','1')
    monkeypatch.setenv('CODE_FIX_EXECUTION_URL','https://execution.example')
    monkeypatch.setenv('CODE_FIX_EXECUTION_TOKEN','x'*40)
    monkeypatch.setenv('CODE_FIX_EXECUTION_APPROVED','1')
    monkeypatch.setenv('CODE_FIX_CONNECTION_CHECK',json.dumps({'isolation_verified':True,
        'checked_at':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}))
    assert module.readiness()['isolation_verified'] is verified

@pytest.mark.parametrize('cause,message',[(ConnectionRefusedError(10061,'hidden endpoint'),'连接被拒绝'),
    (__import__('socket').gaierror(-2,'hidden endpoint'),'无法解析')])
def test_remote_connection_cause_is_classified_without_raw_exception(cause,message):
    from app.remote_runner import RemoteRunner
    def handle(req):raise httpx.ConnectError('hidden endpoint') from cause
    with httpx.Client(base_url='https://execution.example',transport=httpx.MockTransport(handle)) as client:
        runner=RemoteRunner(url='https://execution.example',token='x'*40,client=client)
        with pytest.raises(IsolationBlocked,match=message) as caught:runner.request('GET','/v1/health')
        assert 'hidden endpoint' not in str(caught.value)
