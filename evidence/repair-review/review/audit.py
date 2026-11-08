from pathlib import Path
import json,tempfile,hashlib,subprocess,sys
sys.path.insert(0,str(Path.cwd()))
from app.workflow_v3 import WorkflowRun
from app.tasks_v3 import get_task
from app.runner import code_version
from app.run_store import RunStore
from app.api import create_app
from app.providers import validate_url
from c_runner.runner import _junit_key
from fastapi.testclient import TestClient
r=Path.cwd();out=r/'runs/review-package-check';findings={}
with tempfile.TemporaryDirectory(prefix='review-poc-') as temp:
    t=Path(temp);task=get_task('leap');base=task.prepare(t/'base');current=task.prepare(t/'current')
    payload=b'def is_leap(year):\r\n    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\r\n'
    (current/task.source).write_bytes(payload)
    ctx=object.__new__(WorkflowRun);ctx.base=base;ctx.current=current;ctx.source=task.source;ctx.directory=t/'workflow';ctx.directory.mkdir();ctx.task={'project':'leap'};ctx.version=code_version(current,(task.source,))
    class Halt(Exception):pass
    def intercept(work,phase,name):
        findings['B-01']={'candidate_version':ctx.version,'final_version':code_version(work,(task.source,)),'bytes_equal':(work/task.source).read_bytes()==payload,'method':'production final_check until test dispatch'}
        raise Halt()
    ctx.test=intercept
    try:ctx.final_check()
    except Halt:pass
    log=t/'stdout.txt';log.write_text('FAILED tests/t.py::test_x - assert 3 == 4\n    assert secret == 999\n',encoding='utf-8')
    findings['B-02']={'excerpt':WorkflowRun.excerpt({'stdout_path':'stdout.txt'},t,'stdout_path')}
    findings['B-04']={u:validate_url('siliconflow',u) for u in ('https://api.siliconflow.cn:4443/v1','https://api.siliconflow.cn:80/v1','https://api.siliconflow.cn:abc/v1')}
    with TestClient(create_app(t/'api'),raise_server_exceptions=False) as client:
        response=client.post('/api/v3/tasks',headers=[(b'x-codefix-token',b'\xe9')],json={})
        findings['B-05']={'non_ascii_header_status':response.status_code}
    findings['B-06']={'actual':_junit_key('tests/test_a.py::test_x[a::b]'),'expected':['tests.test_a','test_x[a::b]']}
    store=RunStore(t/'store');run=store.create({'project':'leap','mode':'repair','description':'review fixture','max_calls':3,'max_rounds':1,'total_seconds':60});rid=run['run_id'];store.event(rid,{'event':'review_fixture','raw_text':'REVIEW_SYNTHETIC_VALUE','messages':['synthetic']})
    findings['B-07']={'events_contains_raw':any('raw_text' in e for e in store.events(rid)),'snapshot_contains_raw':any('raw_text' in e for e in store.snapshot(rid)['events'])}
audits={}
for n in ('evidence/integration-docker-loop/C-acceptance-20261106-014017-b626a073','evidence/integration-quixbugs/C-acceptance-20261108-195510-2a5cd7f9'):
    d=r/n;v=json.loads((d/'sha256.json').read_text());index=v.get('sha256',v)
    missing=[];different=[]
    for p,h in index.items():
        f=d/p
        if not f.is_file():missing.append(p)
        elif hashlib.sha256(f.read_bytes()).hexdigest()!=h:different.append(p)
    audits[n]={'files':len(index),'missing':missing,'different':different}
result={'head':subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(),'findings':findings,'evidence_audits':audits,'source_changes':False,'real_model_calls':0,'docker_runs':0}
(out/'current-findings.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
