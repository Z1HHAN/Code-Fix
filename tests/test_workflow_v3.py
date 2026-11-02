"""State-machine checks use explicit model/executor stubs, never model scores."""
import json
from pathlib import Path

import pytest

from app.candidates import SOURCE, apply_candidate
from app.docker_runner import DockerRunner
from app.runner import code_version, prepare_fixture
from app.run_store import RunStore
from app.workflow_v3 import Workflow


class ExecutorStub:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.phases = []
    def preflight(self, folder):
        return {'isolation_ready': True, 'scope': 'test stub only'}
    def run(self, work, folder, *, phase, timeout, expected_version):
        self.phases.append(phase)
        folder.mkdir()
        outcome = next(self.outcomes)
        (folder / 'stdout.txt').write_text('OFFLINE EXECUTOR STUB: '+outcome)
        (folder / 'stderr.txt').write_text('')
        return {'code_version': expected_version, 'phase': phase, 'failure_class': outcome,
                'verification_status': 'passed' if outcome == 'passed' else 'failed',
                'stdout_path': 'stdout.txt', 'stderr_path': 'stderr.txt', 'exit_code': 0 if outcome == 'passed' else 1}


class ModelStub:
    mode = 'test_stub'
    def __init__(self, scheduler='repair'):
        self.roles = []
        self.scheduler = scheduler
    def call(self, messages, **kwargs):
        material = json.loads(messages[1]['content'])
        evidence = material['dev_evidence'][0]
        assert evidence['phase'] == 'development'
        common = {'evidence_ids': [evidence['evidence_id']], 'knowledge_citations': []}
        prompt = messages[0]['content']
        if 'V3检测员' in prompt:
            role = 'detector'
            output = common | {'analysis': 'offline analysis', 'failure_class': evidence['failure_class'], 'suggested_checks': []}
        elif 'V3调度员' in prompt:
            role = 'scheduler'
            output = common | {'action': self.scheduler, 'reason': 'offline advice'}
        elif '定位员提示词' in prompt:
            role = 'locator'
            output = common | {'file': SOURCE, 'function': 'is_leap', 'hypothesis': 'offline hypothesis'}
        else:
            role = 'repairer'
            output = common | {'explanation': 'synthetic candidate', 'changes': [{'path': SOURCE, 'new_content': 'def is_leap(year):\n    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\n'}]}
        self.roles.append(role)
        return {'status': 'ok', 'mode': 'test_stub', 'model_api_called': False, 'raw_text': json.dumps(output)}


def execute(tmp_path, outcomes, task=None, scheduler='repair'):
    store = RunStore(tmp_path)
    run_id = store.create({'mode': 'repair'} | (task or {}))['run_id']
    store.claim(run_id)
    runner, model = ExecutorStub(outcomes), ModelStub(scheduler)
    workflow = Workflow(store, runner, model)
    status, result = workflow.run(run_id)
    store.finish(run_id, status, result)
    return status, result, runner, model, store, run_id


def test_stub_success_requires_independent_call_and_no_final_feedback(tmp_path):
    status, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed', 'passed', 'passed'])
    assert status == 'success' and result['final_result'] == 'passed'
    assert result['mode'] == 'test_stub' and not result['model_api_called']
    assert runner.phases == ['development', 'development', 'final_check']
    assert model.roles == ['repairer', 'detector', 'scheduler']
    assert (store.directory(rid) / 'final-patch/patch.diff').is_file()


def test_final_failure_ends_without_new_model_calls(tmp_path):
    status, result, runner, model, *_ = execute(tmp_path, ['assertion_failed', 'passed', 'assertion_failed'])
    assert status == 'failed' and result['final_result'] == 'failed'
    assert len(model.roles) == 3


@pytest.mark.parametrize('outcome, expected, roles', [
    ('syntax_error', 'success', ['repairer', 'detector', 'scheduler', 'repairer', 'detector', 'scheduler']),
    ('assertion_failed', 'success', ['repairer', 'detector', 'scheduler', 'locator', 'repairer', 'detector', 'scheduler']),
    ('environment_error', 'paused', ['repairer']),
    ('timeout', 'stopped', ['repairer'])])
def test_failure_types_change_actual_next_role(tmp_path, outcome, expected, roles):
    status, result, runner, model, *_ = execute(tmp_path, ['assertion_failed', outcome, 'passed', 'passed'])
    assert status == expected and model.roles == roles


def test_round_limit_stops_and_model_cannot_force_final_check(tmp_path):
    status, result, runner, model, *_ = execute(tmp_path, ['assertion_failed', 'assertion_failed'], {'max_rounds': 1}, 'final_check')
    assert status == 'stopped' and result['rounds_used'] == 1
    assert result['final_result'] == 'not_run' and runner.phases == ['development', 'development']


def test_scheduler_can_pause_but_cannot_forge_success(tmp_path):
    status, result, runner, model, *_ = execute(tmp_path, ['assertion_failed', 'passed'], scheduler='pause')
    assert status == 'paused' and result['final_result'] == 'not_run'


def test_patch_permissions_and_no_overwrite(tmp_path):
    base = prepare_fixture('leap_original', tmp_path / 'base')
    with pytest.raises(ValueError):
        apply_candidate(base, tmp_path / 'bad', [{'path': 'tests/test_leap.py', 'new_content': ''}], tmp_path / 'evidence')
    result = apply_candidate(base, tmp_path / 'candidate', [{'path': SOURCE, 'new_content': 'x=1\n'}], tmp_path / 'evidence')
    assert result['candidate_version'] != code_version(base, (SOURCE,))
    assert (base / 'tests/test_leap.py').read_bytes() == (tmp_path / 'candidate/tests/test_leap.py').read_bytes()


def test_real_environment_blocks_without_execution_approval(tmp_path, monkeypatch):
    monkeypatch.delenv('CODE_FIX_EXECUTION_APPROVED', raising=False)
    report = DockerRunner().preflight(tmp_path)
    assert not report['isolation_ready']
    assert (tmp_path / 'isolation.json').is_file()
