"""Regression boundaries from the C repair packet; synthetic inputs only."""
import json
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.providers import validate_url
from app.runner import code_version
from app.tasks_v3 import get_task
from app.workflow_v3 import WorkflowRun
from c_runner.runner import _junit_key


@pytest.mark.parametrize('corrupt', [False, True])
def test_final_candidate_bytes_bound_before_dispatch(tmp_path, corrupt):
    definition = get_task('leap')
    base = definition.prepare(tmp_path / 'base')
    current = definition.prepare(tmp_path / 'current')
    payload = b'def is_leap(year):\r\n    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\r\n'
    (current / definition.source).write_bytes(payload)
    ctx = object.__new__(WorkflowRun)
    ctx.base, ctx.current, ctx.source = base, current, definition.source
    ctx.directory = tmp_path / 'run'
    ctx.task = {'project': 'leap'}
    ctx.version = code_version(current, (definition.source,))
    ctx.final = {}
    if corrupt:
        (current / definition.source).write_bytes(payload + b'# changed\n')
    class Dispatched(Exception):
        pass
    def test(work, phase, name):
        assert not corrupt, 'changed bytes must never reach final runner'
        assert (work / definition.source).read_bytes() == payload
        assert code_version(work, (definition.source,)) == ctx.version
        metadata = json.loads((ctx.directory / 'final-patch/candidate.json').read_text())
        assert metadata['candidate_version'] == ctx.version
        raise Dispatched()
    ctx.test = test
    from app.docker_runner import IsolationBlocked
    with pytest.raises(IsolationBlocked if corrupt else Dispatched):
        ctx.final_check()


@pytest.mark.parametrize('line', [
    'FAILED tests/t.py::test_x - assert 3 == 4',
    '    assert result == 999',
    '  E   AssertionError: expected-secret',
    'FAILED tests/t.py::test_x - AssertionError: expected-secret',
    '    > assert result == 999',
])
def test_model_excerpt_never_contains_assertion_values(tmp_path, line):
    (tmp_path / 'out.txt').write_text(line, encoding='utf-8')
    text = WorkflowRun.excerpt({'stdout_path': 'out.txt'}, tmp_path, 'stdout_path')
    assert not any(s in text for s in ('3 == 4', '999', 'expected-secret'))
    if line.startswith('FAILED'):
        assert 'tests/t.py::test_x' in text


def test_final_dispatch_rejects_late_source_change(tmp_path):
    from app.docker_runner import IsolationBlocked
    ctx = object.__new__(WorkflowRun)
    ctx.source = 'source.py'
    (tmp_path / ctx.source).write_bytes(b'original\r\n')
    ctx.version = code_version(tmp_path, (ctx.source,))
    ctx.boundary = lambda: None
    (tmp_path / ctx.source).write_bytes(b'changed\r\n')
    with pytest.raises(IsolationBlocked, match='最终候选版本已改变'):
        ctx.test(tmp_path, 'final_check', 'unused')


def test_assertion_values_are_absent_at_gateway_boundary(tmp_path):
    from types import SimpleNamespace
    (tmp_path / 'out.txt').write_text('FAILED tests/t.py::test_x[SECRET] - assert 3 == 4\n    assert value == 999')
    (tmp_path / 'source.py').write_text('pass\n')
    evidence = WorkflowRun.evidence({'stdout_path': 'out.txt', 'failure_class': 'assertion_failed',
                                    'code_version': 'synthetic'}, tmp_path)
    class Captured(Exception):
        pass
    def invoke(role, **kwargs):
        summary = kwargs['evidence']['summary']
        assert 'tests/t.py::test_x' in summary
        assert all(secret not in summary for secret in ('SECRET', '3 == 4', '999'))
        raise Captured()
    ctx = object.__new__(WorkflowRun)
    ctx.boundary = lambda: None
    ctx.model_calls = 0
    ctx.output_feedback = None
    ctx.task = {'max_calls': 1}
    ctx.trace = SimpleNamespace(start=lambda *a, **k: {'step_id': 'step', 'parent_step_id': None})
    ctx.gateway = SimpleNamespace(invoke=invoke)
    ctx.run_id, ctx.version, ctx.deadline = 'synthetic', 'synthetic', 0
    ctx.current, ctx.source = tmp_path, 'source.py'
    with pytest.raises(Captured):
        ctx.call('repairer', evidence, 1)


@pytest.mark.parametrize('port', [':80', ':4443', ':abc', ':65536', ':-1'])
def test_provider_rejects_non_https_or_invalid_ports(port):
    assert not validate_url('siliconflow', 'https://api.siliconflow.cn' + port + '/v1')


@pytest.mark.parametrize('port', ['', ':443'])
def test_provider_accepts_https_port(port):
    assert validate_url('siliconflow', 'https://api.siliconflow.cn' + port + '/v1')


def test_non_ascii_token_is_forbidden_and_valid_session_still_works(tmp_path):
    with TestClient(create_app(tmp_path), raise_server_exceptions=False) as client:
        assert client.post('/api/v3/tasks', headers=[(b'x-codefix-token', b'\xe9')], json={}).status_code == 403
        token = client.get('/api/v3/session').json()['token']
        assert client.post('/api/v3/tasks', headers={'x-codefix-token': token}, json={}).status_code == 201


@pytest.mark.parametrize('nodeid,expected', [
    ('tests/test_a.py::test_x[a::b]', ('tests.test_a', 'test_x[a::b]')),
    ('tests/test_a.py::TestA::test_x[1]', ('tests.test_a.TestA', 'test_x[1]')),
    ('tests/test_a.py::TestA::test_x[a[::]b]', ('tests.test_a.TestA', 'test_x[a[::]b]')),
])
def test_parameterized_junit_identity(nodeid, expected):
    assert _junit_key(nodeid) == expected


def acceptance():
    path = Path(__file__).resolve().parents[1] / 'scripts/c_acceptance.py'
    spec = importlib.util.spec_from_file_location('acceptance_review', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('status,allow,blocked', [('', False, False), (' M app/api.py', False, True),
    (' M app/api.py', True, False), ('UU app/api.py', True, True), ('AD app/api.py', True, True)])
def test_acceptance_dirty_policy(status, allow, blocked):
    assert bool(acceptance().identity_issue({'git_commit': 'synthetic', 'git_status': status}, allow)) == blocked


def test_gate_count_cannot_shrink_with_manifest(tmp_path):
    (tmp_path / 'repair_gates').mkdir()
    (tmp_path / 'acceptance_tests').mkdir()
    (tmp_path / 'acceptance_tests/case_map.json').write_text(json.dumps({
        'required_testcase_count': 25, 'required_testcases': ['only_one']}))
    (tmp_path / 'repair_gates/junit.xml').write_text('<testsuite><testcase classname="probe" name="only_one"/></testsuite>')
    record = {'stage': 'repair_gates', 'status': 'passed', 'exit_code': 0}
    acceptance().assess(record, tmp_path, tmp_path)
    assert record['status'] == 'blocked'


def test_platform_inventory_detects_one_missing_identity(tmp_path):
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / 'configs/acceptance_baseline.json').read_text())
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/acceptance_baseline.json').write_text(json.dumps(baseline))
    import xml.etree.ElementTree as ET
    suite = ET.Element('testsuite')
    for classname, name in baseline['platform_testcases'][1:]:
        ET.SubElement(suite, 'testcase', classname=classname, name=name)
    (tmp_path / 'platform_pytest').mkdir()
    ET.ElementTree(suite).write(tmp_path / 'platform_pytest/junit.xml')
    record = {'stage': 'platform_pytest', 'status': 'passed', 'exit_code': 0}
    acceptance().assess(record, tmp_path, tmp_path)
    assert record['status'] == 'blocked'
    assert record['missing_baseline_testcases'] == [tuple(baseline['platform_testcases'][0])]


def test_node_inventory_detects_one_missing_subtest(tmp_path):
    baseline = json.loads((Path(__file__).resolve().parents[1] / 'configs/acceptance_baseline.json').read_text())
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/acceptance_baseline.json').write_text(json.dumps(baseline))
    (tmp_path / 'node_ui').mkdir()
    text = '\n'.join('# Subtest: ' + n for n in baseline['node_subtests'][1:])
    text += '\n# tests 22\n# pass 22\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n'
    (tmp_path / 'node_ui/stdout.txt').write_text(text)
    record = {'stage': 'node_ui', 'status': 'passed', 'exit_code': 0}
    acceptance().assess(record, tmp_path, tmp_path)
    assert record['status'] == 'blocked'


def test_public_report_and_zip_keep_complete_model_text(tmp_path):
    import io
    import zipfile
    app = create_app(tmp_path)
    store = app.state.service.store
    rid = store.create({'mode': 'repair'})['run_id']
    marker = 'SYNTHETIC MODEL BODY ' * 400
    store.event(rid, {'event': 'agent_call', 'raw_text': marker})
    store.claim(rid)
    store.finish_if_running(rid, 'stopped', {'final_result': 'not_run'})
    with TestClient(app) as client:
        report = client.get(f'/api/v3/tasks/{rid}/report').json()
        assert any(e.get('raw_text') == marker for e in report['events'])
        response = client.get(f'/api/v3/tasks/{rid}/bundle')
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            archived = json.loads(bundle.read('report.json'))
        assert archived == report
