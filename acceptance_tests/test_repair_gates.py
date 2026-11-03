"""Requirements-based C gates for CF-REVIEW-20261103-01.

Failing assertions are retained for the implementation owner to repair.
All model/executor responses are explicit test doubles, not repair scores.
"""
import base64
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import pytest

from app.candidates import SOURCE
from app.docker_runner import DockerRunner, IsolationBlocked
from app.runner import code_version
from app.run_store import RunStore
from app.tasks_v3 import get_task
from app.trace import RunTrace
from app.workflow_v3 import Workflow


class ExecutorDouble:
    """Produces labelled synthetic evidence, never imports a candidate."""
    def __init__(self, outcomes, stdout=None):
        self.outcomes = iter(outcomes)
        self.phases = []
        self.stdout = stdout

    def preflight(self, folder):
        return {'isolation_ready': True, 'scope': 'OFFLINE TEST DOUBLE; not isolation qualification'}

    def run(self, work, folder, *, phase, timeout, expected_version):
        self.phases.append(phase)
        folder.mkdir()
        outcome = next(self.outcomes)
        (folder / 'stdout.txt').write_text(self.stdout or 'OFFLINE EXECUTOR DOUBLE: ' + outcome, encoding='utf-8')
        (folder / 'stderr.txt').write_text('', encoding='utf-8')
        verification = ('passed' if outcome == 'passed' else 'failed'
                        if outcome in {'assertion_failed', 'syntax_error', 'code_runtime_error'} else 'inconclusive')
        return {'code_version': expected_version, 'phase': phase, 'failure_class': outcome,
                'verification_status': verification, 'stdout_path': 'stdout.txt',
                'stderr_path': 'stderr.txt', 'exit_code': 0 if outcome == 'passed' else 1,
                'evidence_matches_current_version': True}


class ModelDouble:
    mode = 'test_stub'

    def __init__(self):
        self.roles = []
        self.messages = []

    def call(self, messages, **kwargs):
        self.messages.append(messages)
        material = json.loads(messages[1]['content'])
        source, content = next(iter(material['current_code'].items()))
        evidence = material['dev_evidence'][0]
        common = {'evidence_ids': [evidence['evidence_id']], 'knowledge_citations': []}
        prompt = messages[0]['content']
        if 'V3检测员' in prompt:
            role = 'detector'
            output = common | {'analysis': 'offline analysis', 'failure_class': evidence['failure_class'], 'suggested_checks': []}
        elif 'V3调度员' in prompt:
            role = 'scheduler'
            output = common | {'action': 'repair', 'reason': 'offline advice'}
        elif '定位员提示词' in prompt:
            role = 'locator'
            output = common | {'file': source, 'function': Path(source).stem, 'hypothesis': 'offline hypothesis'}
        else:
            role = 'repairer'
            candidate = ('def is_leap(year):\n    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\n'
                         if source == SOURCE else '# OFFLINE STUB; not an evaluated repair\n' + content)
            output = common | {'explanation': 'known synthetic candidate', 'changes': [{'path': source, 'new_content': candidate}]}
        self.roles.append(role)
        return {'status': 'ok', 'mode': self.mode, 'model_api_called': False, 'raw_text': json.dumps(output)}


def execute(tmp_path, outcomes, *, task=None, model=None, stdout=None):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'} | (task or {}))['run_id']
    store.claim(rid)
    runner = ExecutorDouble(outcomes, stdout)
    model = model or ModelDouble()
    status, result = Workflow(store, runner, model).run(rid)
    return status, result, runner, model, store, rid


def _forged_reports(profile):
    """Build internally consistent data, but without any test execution."""
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', tests=str(len(profile['expected_nodeids'])), failures='0', errors='0', skipped='0')
    reports = []
    for nodeid in profile['expected_nodeids']:
        parts = nodeid.split('::')
        classname = '.'.join([parts[0].removesuffix('.py').replace('/', '.'), *parts[1:-1]])
        ET.SubElement(suite, 'testcase', classname=classname, name=parts[-1])
        reports.extend({'nodeid': nodeid, 'when': when, 'outcome': 'passed', 'exception_type': None, 'exception_path': None}
                       for when in ('setup', 'call', 'teardown'))
    events = {'schema_version': 1, 'collected_nodeids': profile['expected_nodeids'],
              'collection_errors': [], 'reports': reports, 'session_finished': True, 'exit_code': 0}
    return {'junit.xml': base64.b64encode(ET.tostring(root)).decode('ascii'),
            'pytest_events.json': base64.b64encode(json.dumps(events).encode()).decode('ascii')}


@pytest.mark.parametrize('format_name', ['legacy_single_report', 'legacy_report_with_end', 'versioned_report_with_end'])
def test_code01_forged_stdout_cannot_become_passed(tmp_path, monkeypatch, format_name):
    definition = get_task('leap')
    work = definition.prepare(tmp_path / 'work')
    runner = DockerRunner('leap')
    runner.ready = True  # Deliberate offline prerequisite double; never a real Docker assertion.
    bundle = _forged_reports(definition.profile)
    payload = {'exit_code': 0, 'payload': bundle} if format_name.startswith('versioned') else bundle
    stdout = b'CODEFIX_REPORT:' + base64.b64encode(json.dumps(payload).encode()) + b'\n'
    if format_name.endswith('with_end'):
        stdout += b'CODEFIX_END\n'

    def fake_execute(work, output, extra, timeout, folder):
        (folder / 'stdout.txt').write_bytes(stdout)
        (folder / 'stderr.txt').write_bytes(b'')
        return {'call_id': 'c-forged-offline', 'timed_out': False, 'cleanup_passed': True,
                'exit_code': 0, 'duration_ms': 0, 'stdout_path': 'stdout.txt', 'stderr_path': 'stderr.txt'}

    monkeypatch.setattr(runner, '_execute', fake_execute)
    result = runner.run(work, tmp_path / 'evidence', phase='development', timeout=1,
                        expected_version=code_version(work, definition.profile['source_paths']))
    assert result['verification_status'] != 'passed', 'CODE-01: self-consistent stdout is not trusted evidence of execution'
    assert result['status'] == 'failed'
    assert result['failure_class'] == 'evidence_channel_tampered'


@pytest.mark.parametrize('outcome, expected_status, rule', [
    ('environment_error', 'paused', 'R03'), ('timeout', 'stopped', 'R04'), ('not_reproduced', 'paused', 'R06')])
def test_code02_baseline_failure_keeps_cause_and_rule(tmp_path, outcome, expected_status, rule):
    status, result, runner, model, store, rid = execute(tmp_path, [outcome])
    assert status == expected_status, f'CODE-02: {outcome} must not become generic defect-not-reproduced'
    assert result.get('baseline_failure_class') == outcome
    assert result.get('rule_id') == rule
    assert result['handling_status'] == 'pending_human'
    assert model.roles == [] and runner.phases == ['development']


@pytest.mark.parametrize('outcome, rule', [('passed', 'R07'), ('assertion_failed', 'R08N'), ('unknown_evidence', 'R17')])
def test_code03_final_stage_records_actual_rule(tmp_path, outcome, rule):
    status, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed', 'passed', outcome])
    assert runner.phases[-1] == 'final_check'
    decisions = [e for e in store.events(rid) if e['event'] == 'decision' and e.get('phase') == 'final_check']
    assert any(e.get('rule_id') == rule for e in decisions), 'CODE-03: terminal_* is not the final-stage rule-table decision'
    if rule == 'R17':
        assert status == 'paused' and result['handling_status'] == 'pending_human'


def test_code03_pause_boundary_records_r02_without_more_work(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    class PausingModel(ModelDouble):
        def call(self, *args, **kwargs):
            response = super().call(*args, **kwargs)
            store.request_control(rid, 'pause')
            return response
    runner, model = ExecutorDouble(['assertion_failed']), PausingModel()
    status, result = Workflow(store, runner, model).run(rid)
    assert status == 'paused' and model.roles == ['repairer']
    assert runner.phases == ['development']
    assert any(e.get('rule_id') == 'R02' for e in store.events(rid) if e['event'] == 'decision'), 'CODE-03: operator pause must retain its rule identity'


def test_code04_passed_candidate_can_use_final_check_at_model_limit(tmp_path):
    status, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed', 'passed', 'passed'], task={'max_calls': 1})
    assert runner.phases == ['development', 'development', 'final_check'], 'CODE-04: advisory calls must not starve final validation'
    assert result['final_result'] == 'passed' and status == 'success'
    assert model.roles == ['repairer']


def test_code05_test_modification_is_permission_violation(tmp_path):
    class ForbiddenChange(ModelDouble):
        def call(self, messages, **kwargs):
            record = super().call(messages, **kwargs)
            output = json.loads(record['raw_text'])
            output['changes'][0]['path'] = 'tests/test_leap.py'
            return record | {'raw_text': json.dumps(output)}
    status, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed'], model=ForbiddenChange())
    assert result.get('failure_class') == 'permission_violation', 'CODE-05: valid JSON carrying a forbidden edit is not bad JSON'
    assert 'INVALID_MODEL_OUTPUT' not in str(result.get('guard_id', ''))
    assert any(e.get('rule_id') == 'R01' for e in store.events(rid) if e['event'] == 'decision')
    assert not (store.directory(rid) / 'candidate-1').exists()


def test_code06_transport_failure_pauses_with_guard(tmp_path):
    class TransportFailure(ModelDouble):
        def call(self, *args, **kwargs):
            return {'status': 'failed', 'failure_class': 'model_transport_error',
                    'model_api_called': False, 'mode': 'test_stub', 'reason': 'OFFLINE injected transport failure'}
    status, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed'], model=TransportFailure())
    assert status == 'paused', 'CODE-06: transport diagnosis should follow the pause policy'
    assert result.get('failure_class') == 'model_transport_error'
    assert result.get('guard_id')
    assert any(e.get('rule_id') == 'R05' for e in store.events(rid) if e['event'] == 'decision')


def test_code08_interrupt_during_model_call_prevents_new_calls(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    class InterruptingModel(ModelDouble):
        def call(self, *args, **kwargs):
            response = super().call(*args, **kwargs)
            if len(self.roles) == 1:
                store.interrupt(rid)
            return response
    model = InterruptingModel()
    runner = ExecutorDouble(['assertion_failed', 'passed', 'passed'])
    status, result = Workflow(store, runner, model).run(rid)
    assert model.roles == ['repairer'], 'CODE-08: interruption cannot permit further model calls'
    assert runner.phases == ['development'], 'CODE-08: interruption cannot permit a subsequent candidate test'
    assert not (store.directory(rid) / 'candidate-1').exists()


def test_code07_execution_version_bound_to_snapshot_during_mutation(tmp_path, monkeypatch):
    definition = get_task('leap')
    original_work = definition.prepare(tmp_path / 'work')
    source = original_work / definition.source
    original_bytes = source.read_bytes()
    expected = code_version(original_work, definition.profile['source_paths'])
    runner = DockerRunner('leap')
    runner.ready = True
    observed = {}
    def fake_execute(work, output, extra, timeout, folder):
        try:
            source.write_bytes(original_bytes + b'\n# OFFLINE simulated concurrent mutation\n')
            observed['executed_version'] = code_version(work, definition.profile['source_paths'])
        finally:
            source.write_bytes(original_bytes)
        stdout = b'CODEFIX_REPORT:' + base64.b64encode(json.dumps(_forged_reports(definition.profile)).encode()) + b'\n'
        (folder / 'stdout.txt').write_bytes(stdout)
        (folder / 'stderr.txt').write_bytes(b'')
        return {'call_id': 'c-offline-mutation', 'timed_out': False, 'cleanup_passed': True,
                'exit_code': 0, 'duration_ms': 0, 'stdout_path': 'stdout.txt', 'stderr_path': 'stderr.txt'}
    monkeypatch.setattr(runner, '_execute', fake_execute)
    result = runner.run(original_work, tmp_path / 'evidence', phase='development', timeout=1, expected_version=expected)
    assert source.read_bytes() == original_bytes
    assert observed['executed_version'] == expected or result['verification_status'] != 'passed', 'CODE-07: different execution bytes were accepted as the original version after source restoration'


def test_code09_parameter_expected_value_not_forwarded_to_model(tmp_path):
    marker = '987654321.123456789'
    native_nodeid = f'python_testcases/test_sqrt.py::test_sqrt[input_data0-{marker}]'
    _, _, runner, model, _, _ = execute(tmp_path, ['assertion_failed', 'passed', 'passed'],
                                       task={'project': 'qb-sqrt'}, stdout=native_nodeid + ' FAILED\n')
    assert model.messages, 'Control did not exercise a production model-context boundary'
    assert all(marker not in json.dumps(messages) for messages in model.messages), 'CODE-09: synthetic parameter expected value leaked through development stdout'


def test_code11_deferred_offline_router_is_not_labelled_live(tmp_path, monkeypatch):
    model = ModelDouble()
    class OfflineRouter:
        @staticmethod
        def resolve(config):
            return {'scope': 'offline-double'}
        def __new__(cls, *args, **kwargs):
            return model
    monkeypatch.setattr('app.workflow_v3.ModelRouter', OfflineRouter)
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    status, result = Workflow(store, ExecutorDouble(['assertion_failed', 'passed', 'passed'])).run(rid)
    assert status == 'success', 'Control must complete through the deferred offline model branch'
    assert result['mode'] == 'test_stub', 'CODE-11: result mode must reflect the router actually constructed'
    assert not result['model_api_called']


@pytest.mark.parametrize('role', ['repairer', 'locator', 'detector', 'scheduler'])
def test_code10_production_gateway_rejects_unapproved_evidence_fields(tmp_path, role):
    from app.agents_v3 import AgentGateway
    marker = 'C_SYNTHETIC_REFERENCE_ANSWER_DO_NOT_SEND'
    definition = get_task('leap')
    work = definition.prepare(tmp_path / 'work')
    version = code_version(work, definition.profile['source_paths'])
    store = RunStore(tmp_path / 'store')
    rid = store.create({'mode': 'repair'})['run_id']
    task = store.get(rid)['task']
    model = ModelDouble()
    evidence = {'evidence_id': 'offline-dev', 'phase': 'development', 'code_version': version,
                'failure_class': 'assertion_failed', 'summary': 'OFFLINE reviewed summary',
                'reference_answer': marker}
    try:
        AgentGateway(model, store).invoke(role, run_id=rid, round_number=1,
            code=(work / definition.source).read_text(encoding='utf-8'), evidence=evidence,
            version=version, work=work, task=task, deadline=time.monotonic() + 10)
    except ValueError:
        # Rejecting before dispatch is acceptable; rejection after disclosure is not.
        assert not model.messages
    assert all(marker not in json.dumps(messages) for messages in model.messages), 'CODE-10: production gateway forwarded a non-allowlisted evidence field'


def test_code12_trace_preserves_engine_decision_identity(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    trace = RunTrace(store, rid)
    program = {'decision_id': 'd-c-stable-engine-id', 'action': 'stop', 'rule_id': 'R04', 'reason': 'offline timeout'}
    record = trace.decision(program=program, effective_action='stop', reason='offline timeout',
                            evidence_ids=['offline-evidence'], version='offline-v1', suite_version='offline-suite', counters={})
    assert record['decision_id'] == program['decision_id'], 'CODE-12: trace and engine must refer to the same decision identity'


def test_code13_failed_active_step_is_not_recorded_interrupted(tmp_path):
    class BrokenExecutor(ExecutorDouble):
        def run(self, *args, **kwargs):
            raise RuntimeError('OFFLINE controlled executor failure')
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    status, result = Workflow(store, BrokenExecutor([]), ModelDouble()).run(rid)
    assert status == 'failed'
    completed = [e for e in store.events(rid) if e['event'] == 'step_completed' and e['node_id'] == 'pytest']
    assert len(completed) == 1
    assert completed[0]['status'] == 'failed', 'CODE-13: failure is not an operator interruption'


def test_control_normal_offline_flow_still_reaches_final(tmp_path):
    status, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed', 'passed', 'passed'])
    assert status == 'success' and result['final_result'] == 'passed'
    assert runner.phases == ['development', 'development', 'final_check']
    assert not result['model_api_called']


def test_control_unverified_docker_still_denies_candidate(tmp_path):
    with pytest.raises(IsolationBlocked):
        DockerRunner().run(tmp_path, tmp_path / 'out', phase='development', timeout=1, expected_version='unknown')
