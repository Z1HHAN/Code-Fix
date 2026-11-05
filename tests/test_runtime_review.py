"""Regression cases for input boundaries, diagnostics and bounded admission."""
import hashlib
import json
from threading import Event
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.agents_v3 import readonly_material
from app.api import create_app
from app.diagnostics import record_exception
from app.repair_output import InvalidModelOutput
from app.run_store import RunStore, StateConflict
from app.service import TaskService
from app.split_runner import SplitDockerRunner, FUNCTIONS
from app.docker_runner import IsolationBlocked
from app.version import SERVICE_VERSION
from app.workflow_v3 import Workflow
from tests.test_workflow_v3 import ExecutorStub, ModelStub


def headers(client):
    return {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}


@pytest.mark.parametrize('error', [ValueError, KeyError])
def test_internal_errors_are_500_not_input_or_missing_errors(tmp_path, monkeypatch, error):
    app = create_app(tmp_path)
    def broken():
        raise error('private internal failure')
    monkeypatch.setattr(app.state.service.store, 'list', broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/api/v3/tasks')
        assert response.status_code == 500
        assert 'private internal failure' not in response.text


def test_version_and_precise_json_input_errors(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/v3/session').json()['version'] == SERVICE_VERSION
        assert client.get('/openapi.json').json()['info']['version'] == SERVICE_VERSION
        auth = headers(client)
        rid = client.post('/api/v3/tasks', headers=auth, json={}).json()['run_id']
        for endpoint in ('/api/v3/tasks', f'/api/v3/tasks/{rid}/control'):
            for body in (b'{', b'\xff'):
                assert client.post(endpoint, headers=auth, content=body).status_code == 422
        assert client.post('/api/v3/tasks', headers=auth, json={'project': 'unknown'}).status_code == 422
        assert client.get('/api/v3/tasks/run-' + '0' * 32).status_code == 404


def test_capacity_full_does_not_claim_and_releases_after_completion(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    entered = [Event(), Event()]
    release = Event()
    ids = [app.state.service.store.create({})['run_id'] for _ in range(3)]
    def gated(run_id):
        if run_id in ids[:2]:
            entered[ids.index(run_id)].set()
        assert release.wait(10)
        app.state.service.store.finish_if_running(run_id, 'stopped', {'final_result': 'not_run'})
    monkeypatch.setattr(app.state.service, 'run_claimed', gated)
    with TestClient(app) as client:
        auth = headers(client)
        try:
            for index in range(2):
                assert client.post(f'/api/v3/tasks/{ids[index]}/start', headers=auth).status_code == 202
                assert entered[index].wait(3)
            assert client.post(f'/api/v3/tasks/{ids[2]}/start', headers=auth).status_code == 503
            assert app.state.service.store.get(ids[2])['status'] == 'created'
            assert app.state.service.store.events(ids[2]) == []
            assert client.post(f'/api/v3/tasks/{ids[0]}/start', headers=auth).status_code == 409
        finally:
            release.set()
        deadline = time.monotonic() + 3
        while True:
            response = client.post(f'/api/v3/tasks/{ids[2]}/start', headers=auth)
            if response.status_code == 202 or time.monotonic() >= deadline:
                break
            assert response.status_code == 503
            time.sleep(.01)
        assert response.status_code == 202
    assert app.state.service.store.get(ids[0])['status'] == 'stopped'


def test_failed_claim_releases_admission_slot(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    rid = app.state.service.store.create({})['run_id']
    def conflict(run_id):
        raise StateConflict('simulated claim race')
    monkeypatch.setattr(app.state.service.store, 'claim', conflict)
    with TestClient(app) as client:
        auth = headers(client)
        for _ in range(4):
            assert client.post(f'/api/v3/tasks/{rid}/start', headers=auth).status_code == 409


def test_executor_shutdown_releases_capacity_and_records_interruption(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    class ClosingExecutor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            raise RuntimeError('executor is closing')
    monkeypatch.setattr('app.api.ThreadPoolExecutor', ClosingExecutor)
    with TestClient(create_app(tmp_path)) as client:
        auth = headers(client)
        for _ in range(3):
            rid = client.post('/api/v3/tasks', headers=auth, json={}).json()['run_id']
            response = client.post(f'/api/v3/tasks/{rid}/start', headers=auth)
            assert response.status_code == 503 and response.json()['detail'] == '服务正在关闭'
            assert client.get(f'/api/v3/tasks/{rid}').json()['status'] == 'interrupted'


def test_diagnostic_stack_redacts_env_and_model_keys_without_locals(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('CODE_FIX_API_KEY', 'env-private-key')
    model = SimpleNamespace(redact=lambda value: value.replace('model-private-key', '[REDACTED]'))
    try:
        private_local = 'local-value-must-not-be-recorded'
        raise TypeError('env-private-key model-private-key')
    except TypeError as exc:
        record = record_exception(tmp_path, exc, model=model)
    data = (tmp_path / record['diagnostic_path']).read_text(encoding='utf-8')
    assert record['error_type'] == 'TypeError'
    assert 'Traceback' in data and 'test_diagnostic_stack' in data
    assert 'env-private-key' not in data + caplog.text
    assert 'model-private-key' not in data + caplog.text
    assert private_local not in data + caplog.text


def test_service_unexpected_error_has_stack_and_failed_terminal(tmp_path, monkeypatch):
    service = TaskService(tmp_path)
    rid = service.store.create({})['run_id']
    def broken(*args, **kwargs):
        raise AttributeError('unexpected baseline failure')
    monkeypatch.setattr('app.service.baseline_record', broken)
    run = service.execute(rid)
    assert run['status'] == 'failed' and run['result']['error_type'] == 'AttributeError'
    data = json.loads((service.store.directory(rid) / run['result']['diagnostic_path']).read_text(encoding='utf-8'))
    assert 'broken' in data['traceback'] and 'unexpected baseline failure' in data['traceback']


@pytest.mark.parametrize('stage', ['executor', 'model'])
def test_workflow_and_model_unexpected_errors_have_diagnostics(tmp_path, stage):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    class BrokenExecutor(ExecutorStub):
        def run(self, *args, **kwargs):
            raise OSError('offline executor failure')
    class BrokenModel(ModelStub):
        def call(self, *args, **kwargs):
            raise TypeError('offline model adapter failure')
    runner = BrokenExecutor([]) if stage == 'executor' else ExecutorStub(['assertion_failed'])
    model = BrokenModel() if stage == 'model' else ModelStub()
    status, result = Workflow(store, runner, model).run(rid)
    assert status in {'failed', 'blocked'} and result['final_result'] == 'not_run'
    diagnostics = list((store.directory(rid) / 'diagnostics').glob('*.json'))
    assert len(diagnostics) == 1
    assert 'Traceback' in json.loads(diagnostics[0].read_text(encoding='utf-8'))['traceback']
    assert not (store.directory(rid) / 'candidate-1').exists()


def interface_definition(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('class Interface: pass\n', encoding='utf-8')
    return SimpleNamespace(source='src/main.py', data={'context_paths': [path]},
                           profile={'approved_files': {path: hashlib.sha256(target.read_bytes()).hexdigest()},
                                    'test_paths': ['tests/check.py']})


@pytest.mark.parametrize('path', ['src/helper.py', 'utils.py', 'python_testcases/node.py'])
def test_registered_interface_names_and_hash_binding(tmp_path, path):
    definition = interface_definition(tmp_path, path)
    assert 'class Interface' in readonly_material(definition, tmp_path)[path]
    (tmp_path / path).write_text('changed', encoding='utf-8')
    with pytest.raises(InvalidModelOutput, match='哈希不匹配') as rejected:
        readonly_material(definition, tmp_path)
    assert path in str(rejected.value)


@pytest.mark.parametrize('path', ['tests/check.py', 'python_testcases/test_hidden.py',
                                 'correct_python_programs/helper.py', 'json_testcases/helper.py',
                                 'conftest.py', 'src/main.py'])
def test_test_and_answer_files_never_become_interface_material(tmp_path, path):
    definition = interface_definition(tmp_path, path)
    with pytest.raises(InvalidModelOutput) as rejected:
        readonly_material(definition, tmp_path)
    assert path in str(rejected.value)


def test_unregistered_and_traversing_context_are_rejected(tmp_path):
    definition = interface_definition(tmp_path, 'src/helper.py')
    for path in ('src/unregistered.py', '../outside.py', '/outside.py'):
        definition.data['context_paths'] = [path]
        with pytest.raises(InvalidModelOutput) as rejected:
            readonly_material(definition, tmp_path)
        assert path in str(rejected.value)


@pytest.mark.parametrize('function', ['x():\n    pass\n#', 'class', '', None])
def test_rpc_rejects_invalid_function_before_files_or_docker(tmp_path, monkeypatch, function):
    runner = SplitDockerRunner()
    runner.ready = True
    monkeypatch.setitem(FUNCTIONS, 'leap', function)
    def forbidden(*args, **kwargs):
        pytest.fail('invalid adapter must be rejected before starting Docker')
    monkeypatch.setattr('app.split_runner.subprocess.run', forbidden)
    with pytest.raises(IsolationBlocked, match='identifier'):
        runner.run(tmp_path / 'missing', tmp_path / 'evidence', phase='development', timeout=1, expected_version='unknown')
    assert not (tmp_path / 'evidence').exists()


def test_parse_evidence_rejects_duplicate_collected_nodeids(tmp_path):
    from c_runner.runner import parse_evidence
    profile = {'expected_nodeids': ['t.py::a', 't.py::b'], 'source_paths': [], 'approved_files': {},
               'allowed_skips': {}}
    junit = tmp_path / 'junit.xml'
    with open(junit, 'w') as f:
        f.write('<testsuite><testcase classname="t" name="a"/><testcase classname="t" name="b"/></testsuite>')
    struct = tmp_path / 'events.json'
    # Duplicate nodeid in collection — must not be treated as complete.
    struct.write_text(json.dumps({'collected_nodeids': ['t.py::a', 't.py::a'],
                                  'collection_errors': [], 'reports': [],
                                  'session_finished': True, 'exit_code': 0}))
    result = parse_evidence(junit, struct, profile, 0)
    assert result['verification_status'] == 'inconclusive'
    assert 'collected' in (result.get('reason') or '') or not result['coverage']['expected_collected_match']


def test_parse_evidence_rejects_duplicate_phase_report(tmp_path):
    from c_runner.runner import parse_evidence
    profile = {'expected_nodeids': ['t.py::a'], 'source_paths': [], 'approved_files': {},
               'allowed_skips': {}}
    junit = tmp_path / 'junit.xml'
    with open(junit, 'w') as f:
        f.write('<testsuite><testcase classname="t" name="a"/></testsuite>')
    struct = tmp_path / 'events.json'
    # Same (nodeid, when) reported twice — tampered or buggy hook.
    struct.write_text(json.dumps({'collected_nodeids': ['t.py::a'],
                                  'collection_errors': [], 'reports': [
                                      {'nodeid': 't.py::a', 'when': 'call', 'outcome': 'passed'},
                                      {'nodeid': 't.py::a', 'when': 'call', 'outcome': 'passed'},
                                  ], 'session_finished': True, 'exit_code': 0}))
    result = parse_evidence(junit, struct, profile, 0)
    assert result['verification_status'] == 'inconclusive'
    assert 'duplicate' in result.get('reason', '')


def test_parse_evidence_normal_setup_call_teardown_is_not_duplicate(tmp_path):
    from c_runner.runner import parse_evidence
    profile = {'expected_nodeids': ['t.py::test_x'], 'source_paths': [], 'approved_files': {},
               'allowed_skips': {}}
    junit = tmp_path / 'junit.xml'
    with open(junit, 'w') as f:
        f.write('<testsuite><testcase classname="t" name="test_x"/></testsuite>')
    struct = tmp_path / 'events.json'
    # Normal lifecycle: setup→call→teardown, each once per node.
    struct.write_text(json.dumps({'collected_nodeids': ['t.py::test_x'],
                                  'collection_errors': [], 'reports': [
                                      {'nodeid': 't.py::test_x', 'when': 'setup', 'outcome': 'passed'},
                                      {'nodeid': 't.py::test_x', 'when': 'call', 'outcome': 'passed'},
                                      {'nodeid': 't.py::test_x', 'when': 'teardown', 'outcome': 'passed'},
                                  ], 'session_finished': True, 'exit_code': 0}))
    result = parse_evidence(junit, struct, profile, 0)
    assert result['verification_status'] == 'passed'


def test_role_config_ignores_legacy_agents_when_runtime_v3_present(tmp_path):
    from app.role_config import RoleConfig, digest
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'knowledge/demo_project').mkdir(parents=True)
    for kid in ('project_structure', 'business_rules', 'testing', 'public_tasks'):
        (tmp_path / f'knowledge/demo_project/{kid}.md').write_text('# stub\n', encoding='utf-8')
    for role_dir in ('prompts',):
        (tmp_path / role_dir).mkdir(exist_ok=True)
    for role in ('locator', 'repairer', 'detector', 'scheduler'):
        (tmp_path / f'prompts/{role}.md').write_text('任务与路径约束：stub\n', encoding='utf-8')
    # Legacy agents has different knowledge_ids; runtime_v3 is authoritative.
    config = {
        'runtime_v3': {
            'config_version': '1.0.0', 'protocol': 'codefix-json-v1',
            'models': {'primary': {'binding': 'live_env'}},
            'agents': [
                {'agent_id': 'locator', 'enabled': True, 'role_type': 'llm', 'model_ref': 'primary',
                 'prompt_ref': 'prompts/locator.md', 'tool_allowlist': ['read_file', 'search_knowledge'],
                 'knowledge_ids': ['project_structure', 'business_rules', 'testing', 'public_tasks'],
                 'output_schema': 'LocatorOutput'},
                {'agent_id': 'repairer', 'enabled': True, 'role_type': 'llm', 'model_ref': 'primary',
                 'prompt_ref': 'prompts/repairer.md', 'tool_allowlist': ['read_file', 'search_knowledge'],
                 'knowledge_ids': ['business_rules', 'testing', 'public_tasks'],
                 'output_schema': 'RepairOutput'},
                {'agent_id': 'detector', 'enabled': True, 'role_type': 'llm', 'model_ref': 'primary',
                 'prompt_ref': 'prompts/detector.md', 'tool_allowlist': ['read_file', 'search_knowledge'],
                 'knowledge_ids': ['business_rules', 'testing', 'public_tasks'],
                 'output_schema': 'DetectorOutput'},
                {'agent_id': 'scheduler', 'enabled': True, 'role_type': 'llm', 'model_ref': 'primary',
                 'prompt_ref': 'prompts/scheduler.md', 'tool_allowlist': ['read_file', 'search_knowledge'],
                 'knowledge_ids': ['business_rules', 'testing', 'public_tasks'],
                 'output_schema': 'SchedulerOutput'}
            ]
        },
        # Old structure missing 'public_tasks' from locator knowledge_ids — must be ignored.
        'legacy_agents_note': '以下为Day1历史兼容配置，生产运行时由runtime_v3替代；两个结构不强制同步',
        'agents': [
            {'agent_id': 'locator', 'role_type': 'llm', 'enabled': True, 'model_ref': 'primary',
             'prompt_ref': 'prompts/locator.md', 'tool_allowlist': ['search_code', 'read_file', 'search_knowledge'],
             'knowledge_ids': ['project_structure', 'business_rules', 'testing'],
             'output_schema': 'LocatorOutput', 'responsibility': 'history', 'role': '定位员'}
        ]
    }
    (tmp_path / 'configs/agents.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    rc = RoleConfig(tmp_path)
    # Production config uses runtime_v3 with all four knowledge_ids.
    assert 'public_tasks' in rc.agents['locator']['knowledge_ids']
    # The hash must be deterministic; changing only legacy agents must not affect it.
    sha1 = rc.sha256
    config['agents'].append({'agent_id': 'phantom', 'role_type': 'llm', 'enabled': True, 'model_ref': 'primary',
                             'prompt_ref': 'prompts/locator.md', 'tool_allowlist': [], 'knowledge_ids': [],
                             'output_schema': 'LocatorOutput', 'responsibility': 'history', 'role': 'phantom'})
    (tmp_path / 'configs/agents.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    rc2 = RoleConfig(tmp_path)
    assert rc2.sha256 == sha1


@pytest.mark.parametrize('ext', ['.txt', '.json', '.md'])
def test_non_python_interface_material_is_rejected(tmp_path, ext):
    path = f'src/data{ext}'
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'stub content')
    definition = SimpleNamespace(source='src/main.py', data={'context_paths': [path]},
                                 profile={'approved_files': {path: hashlib.sha256(target.read_bytes()).hexdigest()},
                                          'test_paths': ['tests/check.py']})
    with pytest.raises(InvalidModelOutput) as exc_info:
        readonly_material(definition, tmp_path)
    assert path in str(exc_info.value)

@pytest.mark.parametrize('stream', ['stdout_path', 'stderr_path'])
def test_structured_diagnostics_filter_modules_and_keep_logs(tmp_path, stream):
    from app.workflow_v3 import WorkflowRun
    raw = ("E   ModuleNotFoundError: No module named 'numpy.linalg'\n"
           "E   ImportError: No module named '/private/answer.py'\n"
           "ErrorType: TypeError\n"
           "E   AssertionError: expected-answer\n"
           "E   TokenValueError: confidential\n"
           "> assert result == 'expected-answer'\n"
           "assert hidden == 'expected-answer'\n"
           "  File '/private/trace.py', line 10\n")
    path = tmp_path / 'log.txt'
    path.write_text(raw, encoding='utf-8')
    result = {stream: 'log.txt', 'failure_class': 'environment_error', 'code_version': 'fixture'}
    ev = WorkflowRun.evidence(result, tmp_path)
    diag = json.loads(ev['summary'])['diagnostics']
    assert set(diag['exception_types']) == {'ModuleNotFoundError', 'ImportError', 'TypeError', 'AssertionError'}
    assert diag['missing_modules'] == ['numpy.linalg']
    assert '/private' not in json.dumps(diag) and 'expected-answer' not in json.dumps(diag)
    assert 'TokenValueError' not in json.dumps(diag)
    assert path.read_text(encoding='utf-8') == raw
    assert 'expected-answer' not in WorkflowRun.excerpt(result, tmp_path, stream)


def test_empty_structured_diagnostics_are_explicit_null(tmp_path):
    from app.workflow_v3 import WorkflowRun
    result = {'failure_class': 'passed', 'code_version': 'fixture'}
    assert json.loads(WorkflowRun.evidence(result, tmp_path)['summary'])['diagnostics'] is None
