import json
import sys
import time
from pathlib import Path
import pytest
from app.config import load_task_spec,read_json
from app.decision import DecisionEngine
from app.repair_output import InvalidModelOutput,parse_repair_output
from app.security import PermissionViolation,validate_changes
from app.runner import run_tests
ROOT=Path(__file__).resolve().parents[1]
def test_configs_and_task_parse():
    assert read_json(ROOT/'configs/agents.json')['config_version']=='iteration-0.2-draft'
    assert read_json(ROOT/'configs/decision_rules.json')['interface_version']=='0.2-draft'
    assert load_task_spec(ROOT/'day1-kit/configs/task.json').allowed_paths
def test_rules_distinguish_failures():
    engine=DecisionEngine(read_json(ROOT/'configs/decision_rules.json')); base={'phase':'development','isolation_ready':True,'user_pause_requested':False,'evidence_matches_current_version':True,'repair_budget_available':True,'execution_budget_available':True,'needs_new_repair':True,'next_action_requires_execution_budget':True,'output_agent':None,'retries_left':2}
    assertion=engine.decide({**base,'failure_class':'assertion_failed'},run_id='r',code_version='v',evidence_ids=['e'],decision_id='D1')
    syntax=engine.decide({**base,'failure_class':'syntax_error'},run_id='r',code_version='v',evidence_ids=['e'],decision_id='D2')
    environment=engine.decide({**base,'failure_class':'environment_error'},run_id='r',code_version='v',evidence_ids=['e'],decision_id='D3')
    assert (assertion['action'],assertion['target_agent'])==('locate','locator'); assert (syntax['action'],syntax['target_agent'])==('repair','repairer'); assert environment['action']=='pause'
def test_missing_context_pauses():
    result=DecisionEngine(read_json(ROOT/'configs/decision_rules.json')).decide({},run_id='r',code_version='v',evidence_ids=[],decision_id='D1'); assert result['action']=='pause'
def test_permission_boundary(tmp_path):
    (tmp_path/'leap_app').mkdir()
    with pytest.raises(PermissionViolation): validate_changes([{'path':'tests/test.py','new_content':'x'}],allowed_paths=('leap_app/leap_year.py',),forbidden_paths=('tests/',),work_root=tmp_path)
    with pytest.raises(PermissionViolation): validate_changes([{'path':'../outside.py','new_content':'x'}],allowed_paths=('leap_app/leap_year.py',),forbidden_paths=('tests/',),work_root=tmp_path)
def test_repair_output_json_protocol():
    payload={'changes':[{'path':'leap_app/leap_year.py','new_content':'x'}],'explanation':'fix','evidence_ids':[],'knowledge_citations':[]}; assert parse_repair_output(json.dumps(payload))['changes']
    with pytest.raises(InvalidModelOutput): parse_repair_output('return year % 4 == 0')

def test_runner_does_not_mark_nonzero_or_wrong_count_as_pass(tmp_path):
    result = run_tests(tmp_path, [sys.executable, '-c', "print('4 passed, 0 failed'); raise SystemExit(1)"], 2, tmp_path/'logs', 't001', expected_test_count=4)
    assert result['status'] == 'failed'
    assert result['verification_status'] != 'passed'
    result = run_tests(tmp_path, [sys.executable, '-c', "print('4 passed, 0 failed')"], 2, tmp_path/'logs', 't002', expected_test_count=3)
    assert result['status'] == 'error'
    assert result['failure_class'] == 'not_reproduced'

def test_runner_timeout_is_inconclusive_and_returns(tmp_path):
    started = time.monotonic()
    result = run_tests(tmp_path, [sys.executable, '-c', 'import time; time.sleep(5)'], 1, tmp_path/'logs', 't003')
    assert time.monotonic() - started < 4
    assert result['status'] == 'timeout'
    assert result['verification_status'] == 'inconclusive'
    assert result['failure_class'] == 'timeout'
    assert result['exit_code'] is None
