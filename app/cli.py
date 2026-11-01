from __future__ import annotations
import argparse,json,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from .config import ConfigError,load_task_spec,read_json,validate_draft_configs
from .decision import DecisionEngine
from .repair_output import InvalidModelOutput,parse_locator_output,parse_repair_output
from .runner import code_version,copy_worktree,run_tests,append_event
ROOT=Path(__file__).resolve().parents[1]
def run_baseline()->int:
    task=load_task_spec(ROOT/'day1-kit/configs/task.json'); run_id=datetime.now().strftime('%Y%m%d-%H%M%S'); run_dir=ROOT/'runs'/run_id; work=run_dir/'work'
    copy_worktree(ROOT/'day1-kit',work,('code','tests','configs')); events=run_dir/'events.jsonl'; append_event(events,{'ts':datetime.now(timezone.utc).isoformat(timespec='seconds'),'run_id':run_id,'task_id':task.task_id,'event':'run_started','phase':'development'})
    version=code_version(work,task.allowed_paths); result=run_tests(work,task.test_command,60,run_dir,'t001',task.expected_test_count); result.update({'run_id':run_id,'task_id':task.task_id,'phase':'development','code_version':version,'output_agent':None}); (run_dir/'tool_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    append_event(events,{'ts':datetime.now(timezone.utc).isoformat(timespec='seconds'),'run_id':run_id,'task_id':task.task_id,'event':'test_run','call_id':'t001','failure_class':result['failure_class'],'status':result['status'],'code_version':version,'evidence_id':'e001'})
    append_event(events,{'ts':datetime.now(timezone.utc).isoformat(timespec='seconds'),'run_id':run_id,'task_id':task.task_id,'event':'final','status':'failed' if result['status']!='ok' else 'success','dev_result':result['verification_status'],'final_result':'not_run'})
    print(json.dumps({'run_id':run_id,'tool_result':result,'evidence_path':str(run_dir.relative_to(ROOT))},ensure_ascii=False,indent=2)); return 0

def main()->int:
    parser=argparse.ArgumentParser(description='Code-Fix v0.2 B成员接口与规则检查'); sub=parser.add_subparsers(dest='command',required=True); sub.add_parser('validate-config'); sub.add_parser('decision-fixtures'); sub.add_parser('run-baseline'); p=sub.add_parser('parse-output'); p.add_argument('kind',choices=['locator','repairer']); p.add_argument('path'); args=parser.parse_args()
    try:
        if args.command=='validate-config':
            task=load_task_spec(ROOT/'day1-kit/configs/task.json'); errors=validate_draft_configs(ROOT); print(json.dumps({'task_id':task.task_id,'errors':errors},ensure_ascii=False,indent=2)); return 1 if errors else 0
        if args.command=='run-baseline': return run_baseline()
        if args.command=='parse-output':
            raw=Path(args.path).read_text(encoding='utf-8'); value=parse_locator_output(raw) if args.kind=='locator' else parse_repair_output(raw); print(json.dumps(value,ensure_ascii=False,indent=2)); return 0
        rules=read_json(ROOT/'configs/decision_rules.json'); engine=DecisionEngine(rules); common={'phase':'development','isolation_ready':True,'user_pause_requested':False,'evidence_matches_current_version':True,'repair_budget_available':True,'execution_budget_available':True,'needs_new_repair':True,'next_action_requires_execution_budget':True,'output_agent':None,'retries_left':2}; results=[]
        for i,failure in enumerate(('assertion_failed','syntax_error','environment_error','timeout'),1): results.append(engine.decide({**common,'failure_class':failure},run_id='fixture-run',code_version='fixture:v0',evidence_ids=['fixture-e1'],decision_id=f'D{i:03d}'))
        print(json.dumps(results,ensure_ascii=False,indent=2)); return 0
    except (ConfigError,InvalidModelOutput) as exc: print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False)); return 2
if __name__=='__main__': raise SystemExit(main())
