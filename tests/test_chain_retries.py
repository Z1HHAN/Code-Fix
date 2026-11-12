"""Offline regressions for the desktop repair chain; no external requests."""
import json
import pytest
from app.run_store import RunStore
from app.workflow_v3 import Workflow
from test_workflow_v3 import ExecutorStub, ModelStub


def execute(tmp_path, model, outcomes, **task):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair', **task})['run_id']
    store.claim(rid)
    runner = ExecutorStub(outcomes)
    status, result = Workflow(store, runner, model).run(rid)
    return status, result, runner, store, rid


@pytest.mark.parametrize('strategy', ['fixed', 'dynamic'])
def test_passing_candidate_skips_advice_and_finishes(tmp_path, strategy):
    model = ModelStub(scheduler='pause')
    status, result, runner, store, rid = execute(tmp_path, model, ['assertion_failed', 'passed', 'passed'], strategy=strategy)
    assert status == 'success' and model.roles == ['repairer']
    assert runner.phases == ['development', 'development', 'final_check']
    assert result['actual_model_requests'] == 0 and result['model_attempts'] == 1
    assert any(e['event'] == 'roles_skipped' for e in store.events(rid))


@pytest.mark.parametrize('invalid_count, expected, rounds', [(1, 'success', 2), (2, 'success', 3), (3, 'stopped', 3)])
def test_bad_repair_json_consumes_round_and_uses_valid_evidence(tmp_path, invalid_count, expected, rounds):
    class BadFirst(ModelStub):
        attempts = 0
        def call(self, messages, **kwargs):
            self.attempts += 1
            if self.attempts <= invalid_count:
                return {'status': 'ok', 'raw_text': '{}', 'model_api_called': False}
            assert 'output_validation_error' in messages[1]['content']
            return super().call(messages, **kwargs)
    status, result, runner, store, rid = execute(tmp_path, BadFirst(), ['assertion_failed', 'passed', 'passed'])
    assert status == expected and result['rounds_used'] == rounds
    assert result['model_attempts'] == rounds
    assert runner.phases == (['development'] if expected == 'stopped' else ['development', 'development', 'final_check'])
    assert not (store.directory(rid) / 'candidate-1').exists()


def test_failed_candidate_corrects_detector_once(tmp_path):
    class BadDetector(ModelStub):
        bad = False
        def call(self, messages, **kwargs):
            if 'V3检测员' in messages[0]['content'] and not self.bad:
                self.bad = True
                return {'status': 'ok', 'raw_text': '{}', 'model_api_called': False}
            return super().call(messages, **kwargs)
    status, result, runner, store, rid = execute(tmp_path, BadDetector(), ['assertion_failed', 'assertion_failed', 'passed', 'passed'])
    assert status == 'success' and result['rounds_used'] == 2
    retries = [e for e in store.events(rid) if e['event'] == 'format_retry']
    assert len(retries) == 1 and retries[0]['role'] == 'detector'


def test_unusable_advisory_stops_after_one_correction(tmp_path):
    class Broken(ModelStub):
        def call(self, messages, **kwargs):
            if 'V3检测员' in messages[0]['content']:
                return {'status': 'ok', 'raw_text': '{}', 'model_api_called': False}
            return super().call(messages, **kwargs)
    status, result, runner, *_ = execute(tmp_path, Broken(), ['assertion_failed', 'assertion_failed'])
    assert status == 'blocked' and result['model_attempts'] == 3
    assert '格式纠正后仍无效' in result['reason']


def test_scheduler_format_correction_remains_bounded(tmp_path):
    class BadScheduler(ModelStub):
        bad = False
        def call(self, messages, **kwargs):
            if 'V3调度员' in messages[0]['content'] and not self.bad:
                self.bad = True
                return {'status': 'ok', 'raw_text': '{}', 'model_api_called': False}
            return super().call(messages, **kwargs)
    status, result, runner, store, rid = execute(tmp_path, BadScheduler(), ['assertion_failed', 'assertion_failed', 'passed', 'passed'])
    assert status == 'success'
    retries = [e for e in store.events(rid) if e['event'] == 'format_retry']
    assert len(retries) == 1 and retries[0]['role'] == 'scheduler'


@pytest.mark.parametrize('final_outcome,status', [('passed', 'success'), ('assertion_failed', 'failed')])
def test_last_round_last_model_call_still_gets_final_check(tmp_path, final_outcome, status):
    actual, result, runner, *_ = execute(tmp_path, ModelStub(), ['assertion_failed', 'passed', final_outcome], max_rounds=1, max_calls=1)
    assert actual == status and runner.phases[-1] == 'final_check'
    assert result['model_attempts'] == 1
