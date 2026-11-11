"""Cloud protocol tests: synthetic runner only, no generated code executed."""
import base64
import hashlib
import json
from pathlib import Path
import threading
import time
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import pytest

from app.docker_runner import IsolationBlocked
from app.remote_execution import create_execution_app, PROTOCOL, validate_job
from app.remote_runner import RemoteRunner, execution_runner
from app.runner import code_version
from app.tasks_v3 import get_task

TOKEN = 'test-access-code-' + 'a' * 32
OTHER = 'test-access-code-' + 'b' * 32
TOKENS = {'teacher': hashlib.sha256(TOKEN.encode()).hexdigest(), 'tester': hashlib.sha256(OTHER.encode()).hexdigest()}
IMAGE = 'sha256:' + 'a' * 64
AUTH = {'Authorization': 'Bearer ' + TOKEN}


class SyntheticRunner:
    calls = 0
    def __init__(self, project):
        self.task = get_task(project)

    def preflight(self, folder):
        folder.mkdir()
        result = {'isolation_ready': True, 'image': IMAGE, 'scope': 'synthetic protocol test only'}
        (folder / 'isolation.json').write_text(json.dumps(result))
        return result

    def run(self, work, folder, *, phase, timeout, expected_version):
        type(self).calls += 1
        assert timeout <= 60
        assert code_version(work, (self.task.source,)) == expected_version
        output = folder / 'trusted-run/output'
        output.mkdir(parents=True)
        for name in ('stdout.txt', 'stderr.txt'):
            (folder / 'trusted-run' / name).write_text('synthetic fixture only')
        from c_runner.runner import _junit_key, parse_evidence
        root = ET.Element('testsuites')
        suite = ET.SubElement(root, 'testsuite')
        reports = []
        expected = self.task.profile['expected_nodeids']
        for nodeid in expected:
            cls, name = _junit_key(nodeid)
            ET.SubElement(suite, 'testcase', classname=cls, name=name)
            reports.extend({'nodeid': nodeid, 'when': when, 'outcome': 'passed'} for when in ('setup', 'call', 'teardown'))
        ET.ElementTree(root).write(output / 'junit.xml', encoding='utf-8')
        (output / 'pytest_events.json').write_text(json.dumps({'collected_nodeids': expected,
            'reports': reports, 'collection_errors': [], 'session_finished': True, 'exit_code': 0}))
        result = {'project': self.task.project, 'phase': phase, 'code_version': expected_version,
                  'test_suite_version': self.task.summary()['test_suite_version'],
                  'evidence_matches_current_version': True, 'verification_status': 'passed',
                  'failure_class': 'passed', 'status': 'ok', 'exit_code': 0,
                  'stdout_path': 'trusted-run/stdout.txt', 'stderr_path': 'trusted-run/stderr.txt',
                  'junit_path': 'trusted-run/output/junit.xml', 'structured_path': 'trusted-run/output/pytest_events.json'}
        result.update(parse_evidence(output / 'junit.xml', output / 'pytest_events.json', self.task.profile, 0))
        (folder / 'result.json').write_text(json.dumps(result))
        return result


def probe(rid='request-1'):
    return dict(protocol=PROTOCOL, kind='probe', project='leap', request_id=rid,
                expires_in=60, test_seconds=30, test_suite_version=get_task().summary()['test_suite_version'])


def terminal(client, jid, headers=AUTH):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get('/v1/jobs/' + jid, headers=headers).json()
        if result['status'] in {'completed', 'failed', 'cancelled', 'expired', 'interrupted'}:
            return result
        time.sleep(.01)
    pytest.fail('synthetic job did not finish')


def test_authentication_ownership_idempotency_and_no_source_export(tmp_path):
    with TestClient(create_execution_app(tmp_path, TOKENS, runner_factory=SyntheticRunner)) as client:
        assert client.get('/v1/health').status_code == 401
        assert client.get('/v1/health', headers=AUTH).json()['concurrency'] == 1
        first = client.post('/v1/jobs', json=probe(), headers=AUTH)
        assert first.status_code == 202
        jid = first.json()['job_id']
        assert terminal(client, jid)['status'] == 'completed'
        repeated = client.post('/v1/jobs', json=probe(), headers=AUTH).json()
        assert repeated['job_id'] == jid
        assert client.post('/v1/jobs', json=probe() | {'expires_in': 55}, headers=AUTH).status_code == 409
        outsider = {'Authorization': 'Bearer ' + OTHER}
        for suffix in ('', '/evidence'):
            assert client.get('/v1/jobs/' + jid + suffix, headers=outsider).status_code == 404
        assert client.post('/v1/jobs/' + jid + '/cancel', headers=outsider).status_code == 404
        evidence = client.get('/v1/jobs/' + jid + '/evidence', headers=AUTH).json()
        assert set(evidence['files']) == {'isolation/isolation.json'}
        assert TOKEN not in json.dumps(evidence)


@pytest.mark.parametrize('change', [
    {'api_key': 'do-not-send'}, {'command': 'python'}, {'source': 'not for probe'},
    {'test_suite_version': 'wrong'}, {'expires_in': float('inf')}, {'test_seconds': 61},
    {'project': '../../host'}, {'request_id': '../file'}, {'kind': 'shell'},
])
def test_invalid_requests_never_reach_executor(change):
    with pytest.raises(ValueError):
        validate_job(probe() | change)


def test_full_client_server_round_trip_and_frozen_final(tmp_path):
    app = create_execution_app(tmp_path / 'server', TOKENS, runner_factory=SyntheticRunner)
    with TestClient(app) as client:
        runner = RemoteRunner(url='https://execution.example', token=TOKEN, client=client)
        assert runner.preflight(tmp_path / 'probe')['isolation_ready']
        work = get_task().prepare(tmp_path / 'work')
        version = code_version(work, (get_task().source,))
        for phase in ('development', 'final_check'):
            result = runner.run(work, tmp_path / phase, phase=phase, timeout=10, expected_version=version)
            assert result['execution_mode'] == 'remote-docker'
            assert result['verification_status'] == 'passed'
            assert result['transport_mode'] == 'test_transport'
            assert result['code_version'] == version
            assert (tmp_path / phase / result['stdout_path']).read_text() == 'synthetic fixture only'
        assert len(list((tmp_path / 'server').glob('job-*/workspace'))) == 2
        assert not list((tmp_path / 'development').rglob('test_leap.py'))
        (work / get_task().source).write_text('# changed')
        with pytest.raises(IsolationBlocked, match='候选版本'):
            runner.run(work, tmp_path / 'changed', phase='final_check', timeout=10, expected_version=version)


def test_queue_cancellation_capacity_and_expiry(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class Waiting(SyntheticRunner):
        def preflight(self, folder):
            entered.set()
            assert release.wait(5)
            return super().preflight(folder)
    app = create_execution_app(tmp_path, TOKENS, runner_factory=Waiting)
    with TestClient(app) as client:
        try:
            first = client.post('/v1/jobs', json=probe('first'), headers=AUTH).json()['job_id']
            assert entered.wait(2)
            second = client.post('/v1/jobs', json=probe('second'), headers=AUTH).json()['job_id']
            expiring = client.post('/v1/jobs', json=probe('expiring') | {'expires_in': .02}, headers=AUTH).json()['job_id']
            assert client.get('/v1/jobs/' + second, headers=AUTH).json()['status'] == 'queued'
            assert client.post('/v1/jobs/' + second + '/cancel', headers=AUTH).json()['status'] == 'cancel_requested'
            for n in range(5):
                assert client.post('/v1/jobs', json=probe('extra-' + str(n)), headers=AUTH).status_code == 202
            assert client.post('/v1/jobs', json=probe('over-limit'), headers=AUTH).status_code == 429
            time.sleep(.03)
        finally:
            release.set()
        assert terminal(client, first)['status'] == 'completed'
        assert terminal(client, second)['status'] == 'cancelled'
        assert terminal(client, expiring)['status'] == 'expired'


def test_restart_keeps_history_and_does_not_replay(tmp_path):
    with TestClient(create_execution_app(tmp_path, TOKENS, runner_factory=SyntheticRunner)) as client:
        jid = client.post('/v1/jobs', json=probe(), headers=AUTH).json()['job_id']
        assert terminal(client, jid)['status'] == 'completed'
    path = tmp_path / jid / 'job.json'
    record = json.loads(path.read_text())
    record['status'] = 'running'
    path.write_text(json.dumps(record))
    class Forbidden:
        def __init__(self, *args):
            pytest.fail('restart must never replay a job')
    with TestClient(create_execution_app(tmp_path, TOKENS, runner_factory=Forbidden)) as client:
        assert client.get('/v1/jobs/' + jid, headers=AUTH).json()['status'] == 'interrupted'
        assert client.post('/v1/jobs', json=probe(), headers=AUTH).json()['job_id'] == jid


@pytest.mark.parametrize('name,record', [
    ('../source.py', {'sha256': 'x', 'base64': ''}),
    ('test/result.json', {'sha256': 'wrong', 'base64': base64.b64encode(b'{}').decode()}),
    ('test/result.json', {'sha256': 'wrong', 'base64': '!notbase64!'}),
])
def test_client_rejects_paths_and_corrupt_evidence(name, record):
    with pytest.raises(IsolationBlocked):
        RemoteRunner.files({'files': {name: record}})


def test_remote_factory_never_silently_falls_back_to_host(monkeypatch):
    monkeypatch.setenv('CODE_FIX_EXECUTION_URL', 'http://not-https.example')
    with pytest.raises(IsolationBlocked, match='HTTPS'):
        execution_runner('leap')


def test_second_scheduler_cannot_own_same_directory(tmp_path):
    with TestClient(create_execution_app(tmp_path, TOKENS, runner_factory=SyntheticRunner)):
        with pytest.raises(RuntimeError, match='调度进程'):
            with TestClient(create_execution_app(tmp_path, TOKENS, runner_factory=SyntheticRunner)):
                pass


def test_access_revocation_applies_without_restart(tmp_path, monkeypatch):
    path = tmp_path / 'access.json'
    path.write_text(json.dumps(TOKENS))
    monkeypatch.setenv('CODE_FIX_EXECUTION_TOKENS_FILE', str(path))
    with TestClient(create_execution_app(tmp_path / 'jobs', runner_factory=SyntheticRunner)) as client:
        assert client.get('/v1/health', headers=AUTH).status_code == 200
        path.write_text(json.dumps({'tester': TOKENS['tester']}))
        assert client.get('/v1/health', headers=AUTH).status_code == 401
        path.write_text('not valid json')
        assert client.get('/v1/health', headers={'Authorization': 'Bearer ' + OTHER}).status_code == 503


def test_server_rejects_mismatched_candidate_and_frozen_image(tmp_path):
    work = get_task().prepare(tmp_path / 'work')
    value = probe() | {'kind': 'test', 'phase': 'final_check', 'image': IMAGE,
                      'source': (work / get_task().source).read_text(),
                      'code_version': 'sha256:' + 'f' * 64}
    with TestClient(create_execution_app(tmp_path / 'jobs', TOKENS, runner_factory=SyntheticRunner)) as client:
        jid = client.post('/v1/jobs', json=value, headers=AUTH).json()['job_id']
        result = terminal(client, jid)
        assert result['status'] == 'failed'
        assert '候选版本' in result['reason']
        assert client.get('/v1/jobs/' + jid + '/evidence', headers=AUTH).status_code == 409
        value.update(request_id='another', code_version=code_version(work, (get_task().source,)), image='sha256:' + 'b' * 64)
        jid = client.post('/v1/jobs', json=value, headers=AUTH).json()['job_id']
        assert '镜像' in terminal(client, jid)['reason']


def test_client_rechecks_reports_instead_of_trusting_pass_label(tmp_path):
    class Forged(SyntheticRunner):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            folder = args[1]
            (folder / 'trusted-run/output/junit.xml').write_text('<testsuites/>')
            return result
    with TestClient(create_execution_app(tmp_path / 'jobs', TOKENS, runner_factory=Forged)) as client:
        runner = RemoteRunner(url='https://execution.example', token=TOKEN, client=client)
        assert runner.preflight(tmp_path / 'probe')['isolation_ready']
        work = get_task().prepare(tmp_path / 'work')
        with pytest.raises(IsolationBlocked, match='原始测试报告'):
            runner.run(work, tmp_path / 'result', phase='final_check', timeout=10,
                       expected_version=code_version(work, (get_task().source,)))


def test_cloud_upload_contains_only_code_and_registered_assets(tmp_path):
    import zipfile
    from scripts.build_execution_package import build
    target = tmp_path / 'execution.zip'
    build(target)
    with zipfile.ZipFile(target) as archive:
        assert all((entry.external_attr >> 16) & 0o777 == 0o644 for entry in archive.infolist())
        names = set(archive.namelist())
        assert 'configs/tasks/catalog-v3.json' in names
        assert 'scripts/verify_execution_package.py' in names
        assert 'configs/model_profiles.json' not in names
        assert 'configs/agents.json' not in names
        assert not any(n.startswith(('secrets/', 'runs/', 'evidence/', '.git/')) for n in names)
        assert not any('correct_python_programs' in n for n in names)
        manifest = json.loads(archive.read('execution-package-manifest.json'))
        assert set(manifest['files']) == names - {'execution-package-manifest.json'}
        for name, expected in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
        assert b'\r\n' not in archive.read('scripts/prepare_execution_cloud.sh')


def test_real_transport_error_never_resubmits_job(tmp_path):
    import httpx
    calls = []
    def broken(request):
        calls.append((request.method, request.url.path))
        raise httpx.ReadTimeout('network failed')
    with httpx.Client(base_url='https://execution.example', transport=httpx.MockTransport(broken)) as client:
        runner = RemoteRunner(url='https://execution.example', token=TOKEN, client=client)
        result = runner.preflight(tmp_path)
    assert result['isolation_ready'] is False
    assert calls == [('POST', '/v1/jobs')]
