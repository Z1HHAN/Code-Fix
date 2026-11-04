from pathlib import Path
import json
from app.run_store import RunStore
from app.trace import RunTrace
root=Path('runs/trace-panel-platform')
assert not root.exists(),'Use a fresh fixture root'
store=RunStore(root)
run=store.create({'mode':'repair'});rid=run['run_id'];store.claim(rid)
trace=RunTrace(store,rid)
trace.start('repairer',round_number=1)
store.event(rid,{'event':'agent_call',**trace.active,'role':'repairer','mode':'test_stub',
                'model_api_called':False,'status':'ok'})
trace.complete('ok')
step=trace.start('pytest',round_number=1,evidence_ids=['fixture-test'])
folder=store.directory(rid)/'fixture-test';folder.mkdir()
(folder/'stdout.txt').write_text('离线展示夹具日志：模拟断言失败。未执行模型、Docker或pytest。',encoding='utf-8')
store.event(rid,{'event':'test_completed',**step,'evidence_id':'fixture-test','evidence_path':'fixture-test',
                'stdout_path':'stdout.txt','verification_status':'failed','failure_class':'assertion_failed'})
trace.complete('ok')
trace.decision(program={'rule_id':'fixture-budget-rule','action':'stop'},effective_action='stop',reason='离线展示夹具：模拟达到调用上限',
    evidence_ids=['fixture-test'],version='fixture-only',suite_version='fixture-only',counters={},
    advice={'action':'repair','reason':'离线建议'},override_reason='模拟预算不足',round_number=1)
result={'mode':'test_stub','model_api_called':False,'model_calls':0,'tool_calls':0,'rounds_used':1,
        'dev_result':'failed','final_result':'not_run','reason':'仅供面板离线展示，不是修复运行',
        'cost':{'status':'not_applicable'}}
store.finish(rid,'stopped',result)
old=store.create({'mode':'repair'});legacy=old['run_id'];store.claim(legacy)
store.event(legacy,{'event':'decision','action':'pause','reason':'历史展示夹具，无step_id和父节点'})
store.finish(legacy,'paused',{'mode':'test_stub','model_api_called':False,'final_result':'not_run'})
(root/'seed.json').write_text(json.dumps({'trace_run':rid,'legacy_run':legacy,'scope':'offline UI fixtures only'},indent=2),encoding='utf-8')
print((root/'seed.json').read_text())
