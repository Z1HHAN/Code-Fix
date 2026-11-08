"""Approved second repair batch: synthetic transports/processes, no paid calls."""
import ctypes
from ctypes import wintypes as W
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from app.live_model import Ledger, LiveModel, LiveSettings, ModelBlocked
from app.split_runner import SplitDockerRunner


def settings():
    return LiveSettings('synthetic-secret', 'offline', 'https://api.invalid/v1', 1,
                        Decimal('1'), Decimal('1'), Decimal('1'))


def legacy(monkeypatch):
    for name, value in {'LIVE_APPROVED': '1', 'PROVIDER_ID': 'siliconflow',
                        'BASE_URL': 'https://api.siliconflow.cn/v1', 'API_KEY': 'synthetic-secret',
                        'MODEL': 'offline', 'REQUEST_SECONDS': '2', 'DAILY_USD': '1',
                        'INPUT_USD_PER_MILLION': '1', 'OUTPUT_USD_PER_MILLION': '1'}.items():
        monkeypatch.setenv('CODE_FIX_' + name, value)


def test_legacy_migrates_provider_identity(monkeypatch):
    legacy(monkeypatch)
    result = LiveSettings.from_env()
    assert result.provider_id == 'siliconflow'
    assert result.base_url == 'https://api.siliconflow.cn/v1'


@pytest.mark.parametrize('provider,url', [('', 'https://api.siliconflow.cn/v1'),
    ('unknown', 'https://api.siliconflow.cn/v1'), ('siliconflow', 'https://example.com/v1'),
    ('siliconflow', 'https://api.siliconflow.cn:4443/v1'),
    ('aliyun_maas', 'https://api.siliconflow.cn/v1')])
def test_legacy_rejects_before_reading_key(monkeypatch, provider, url):
    legacy(monkeypatch)
    monkeypatch.setenv('CODE_FIX_PROVIDER_ID', provider)
    monkeypatch.setenv('CODE_FIX_BASE_URL', url)
    original = os.environ.get
    def read(name, *default):
        assert name not in {'CODE_FIX_API_KEY', 'OPENAI_API_KEY'}, 'must validate before credentials'
        return original(name, *default)
    monkeypatch.setattr(os.environ, 'get', read)
    with pytest.raises(ModelBlocked, match='迁移配置'):
        LiveSettings.from_env()


def test_unsent_deadline_releases_reservation(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path)
    model = LiveModel(settings(), ledger, transport=httpx.MockTransport(lambda r: pytest.fail('no send')))
    ticks = iter([10, 10, 20])
    with monkeypatch.context() as patch:
        patch.setattr('app.live_model.time.monotonic', lambda: next(ticks, 20))
        record = model.call([], run_id='r', call_id='c', max_calls=2, deadline=15)
    assert not record['transport_request_started']
    assert record['failure_class'] == 'model_deadline_before_send'
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT status,actual_microusd FROM calls').fetchone() == ('known', 0)


@pytest.mark.parametrize('payload', [None, [], {}, {'usage': {'prompt_tokens': 0, 'completion_tokens': 0},
    'choices': [{'message': {'content': ''}}]}])
def test_invalid_200_has_explicit_class(tmp_path, payload):
    model = LiveModel(settings(), Ledger(tmp_path), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=b'' if payload is None else json.dumps(payload).encode())))
    record = model.call([], run_id='r', call_id='c', max_calls=2, deadline=time.monotonic()+5)
    assert record['failure_class'] == 'invalid_model_response'
    assert record['status'] != 'ok'


def test_service_exception_redacts_observed_non_environment_secret(tmp_path, monkeypatch, caplog):
    from app.service import TaskService
    from app.redaction import register_secret
    service = TaskService(tmp_path)
    rid = service.store.create({})['run_id']
    def fail(*a, **k):
        register_secret('synthetic-dpapi-secret')
        raise RuntimeError('failed using synthetic-dpapi-secret')
    monkeypatch.setattr('app.service.baseline_record', fail)
    service.execute(rid)
    report = service.report(rid)
    result = service.store.get(rid)['result']
    diagnostic = json.loads((service.store.directory(rid) / result['diagnostic_path']).read_text())
    assert diagnostic['error_type'] == 'RuntimeError'
    assert '[REDACTED]' in diagnostic['traceback']
    assert 'synthetic-dpapi-secret' not in caplog.text + json.dumps(report) + json.dumps(diagnostic)


def test_profile_cache_isolated_and_invalidates(tmp_path, monkeypatch):
    import c_runner.runner as runner
    runner._profile_registry.cache_clear()
    monkeypatch.setattr(runner, 'PACKAGE', tmp_path)
    path = tmp_path / 'profiles.json'
    profile = {'approved_files': {}, 'test_paths': [], 'expected_nodeids': ['a']}
    path.write_text(json.dumps({'schema_version': 1, 'profiles': {'p': profile}}))
    first = runner._profile('p')
    first['expected_nodeids'].append('mutated')
    assert runner._profile('p')['expected_nodeids'] == ['a']
    assert runner._profile_registry.cache_info().hits == 1
    profile['expected_nodeids'] = ['longer-nodeid']
    path.write_text(json.dumps({'schema_version': 1, 'profiles': {'p': profile}}))
    assert runner._profile('p')['expected_nodeids'] == ['longer-nodeid']


@pytest.mark.parametrize('mode', ['missing', 'nonzero', 'oversized'])
def test_audit_fail_closed(tmp_path, monkeypatch, mode):
    def logs(*a, **k):
        if mode == 'missing':
            raise OSError('unavailable')
        return subprocess.CompletedProcess([], 1 if mode == 'nonzero' else 0, b'x' * 1_048_577, b'')
    monkeypatch.setattr('app.split_runner.subprocess.run', logs)
    result = SplitDockerRunner()._collect_candidate_audit('synthetic', tmp_path)
    assert result['violation'] == 'environment_error'


def test_audit_retains_early_violation_and_no_rotation(tmp_path, monkeypatch):
    runner = SplitDockerRunner()
    runner.executable, runner.image = 'synthetic', 'sha256:' + 'a'*64
    work = runner.task.prepare(tmp_path / 'w')
    command = runner._candidate_command('synthetic', work, 'volume')
    assert '--log-driver=json-file' in command
    assert not any('max-size' in x or 'max-file' in x for x in command)
    raw = b'CODEFIX_AUDIT {"event":"tests_write"}\n' + b'x'*10000
    monkeypatch.setattr('app.split_runner.subprocess.run', lambda *a, **k: subprocess.CompletedProcess([], 0, raw, b''))
    assert runner._collect_candidate_audit('synthetic', tmp_path)['violation'] == 'security_violation'


def test_bundle_counts_actual_bytes_after_stat(tmp_path, monkeypatch):
    import app.bundle as bundle
    from app.run_store import StateConflict
    path = tmp_path / 'grows.txt'
    path.write_bytes(b'x')
    original = Path.open
    def open_growing(self, mode='r', *a, **k):
        if self == path and mode == 'rb':
            return io.BytesIO(b'x'*100)
        return original(self, mode, *a, **k)
    monkeypatch.setattr(bundle, 'LIMIT', 50)
    monkeypatch.setattr(Path, 'open', open_growing)
    with pytest.raises(StateConflict, match='64MiB'):
        bundle.build_bundle(tmp_path, {})


def test_bundle_streams_files_without_read_bytes(tmp_path, monkeypatch):
    import hashlib
    import zipfile
    from app.bundle import build_bundle
    data = b'synthetic evidence\r\n' * 10000
    (tmp_path / 'log.txt').write_bytes(data)
    monkeypatch.setattr(Path, 'read_bytes', lambda *a: pytest.fail('whole-file read'))
    with build_bundle(tmp_path, {'raw_text': 'complete'}) as buffer, zipfile.ZipFile(buffer) as archive:
        assert archive.read('log.txt') == data
        hashes = json.loads(archive.read('sha256.json'))
        assert hashes['log.txt'] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize('mode', ['normal', 'overflow', 'failed'])
def test_live_audit_collector_handles_real_pipes(tmp_path, monkeypatch, mode):
    import app.candidate_logs as logs
    original = subprocess.Popen
    code = "import sys; print('ok'); print('audit',file=sys.stderr)"
    if mode == 'overflow':
        code = "print('x'*10000)"
    elif mode == 'failed':
        code = 'raise SystemExit(1)'
    monkeypatch.setattr(logs.subprocess, 'Popen', lambda *a, **k: original([sys.executable, '-c', code], **k))
    calls = []
    def control(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b'', b'')
    monkeypatch.setattr(logs.subprocess, 'run', control)
    capture = logs.CandidateLogs('synthetic', 'candidate', tmp_path, limit=128)
    capture.process.wait(timeout=5)
    complete, exceeded = capture.finish()
    assert complete == (mode == 'normal')
    assert exceeded == (mode == 'overflow')
    assert sum(p.stat().st_size for p in tmp_path.glob('candidate-*.txt')) <= 128
    if mode == 'overflow':
        assert any('kill' in command for command in calls)


def test_runtime_tool_limit_blocks_before_dispatch(tmp_path):
    from app.runner import code_version
    from app.workflow_v3 import WorkflowRun
    ctx = object.__new__(WorkflowRun)
    ctx.boundary = lambda: None
    ctx.source = 'source.py'
    (tmp_path / ctx.source).write_text('pass\n')
    ctx.version = code_version(tmp_path, (ctx.source,))
    ctx.tool_calls = 5
    with pytest.raises(ModelBlocked, match='工具调用次数'):
        ctx.test(tmp_path, 'final_check', 'never-created')
    assert not (tmp_path / 'never-created').exists()


def test_route_uses_actual_isolation_state():
    from app.workflow_v3 import WorkflowRun, ROOT
    from app.decision import DecisionEngine
    ctx = object.__new__(WorkflowRun)
    ctx.final = {'isolation_ready': False, 'rounds_used': 0, 'test_suite_version': 'synthetic'}
    ctx.task, ctx.run_id = {'max_rounds': 3}, 'synthetic'
    ctx.deadline, ctx.latest_evidence = time.monotonic()+10, []
    ctx.engine = DecisionEngine(json.loads((ROOT / 'configs/decision_rules.json').read_text(encoding='utf-8')))
    ctx.counters = lambda: {}
    ctx.trace = SimpleNamespace(decision=lambda **kwargs: None)
    assert ctx.route('passed')['rule_id'] == 'R00'


def test_success_trace_is_ok():
    from app.workflow_v3 import WorkflowRun
    ctx = object.__new__(WorkflowRun)
    ctx.final = {'rounds_used': 0, 'test_suite_version': 'synthetic'}
    ctx.model_calls = ctx.tool_calls = 0
    ctx.counters = lambda: {'cost': {}}
    ctx.latest_evidence = []
    observed = []
    ctx.trace = SimpleNamespace(complete=lambda status, **k: observed.append(status), decision=lambda **k: None)
    assert ctx.finish('success', 'synthetic')[0] == 'success'
    assert observed == ['ok']


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object contract')
@pytest.mark.parametrize('parent_waits', [False, True])
def test_windows_job_reaps_real_children_and_grandchildren(tmp_path, parent_waits):
    from c_runner.windows_job import WindowsJob
    pidfile = tmp_path / 'pids.json'
    # All scripts are trusted synthetic sleepers, never candidate code.
    child = "import subprocess,sys,time,json,os; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
    child += f"open({str(pidfile)!r},'w').write(json.dumps([os.getpid(),p.pid])); time.sleep(60)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
    parent += 'time.sleep(60)' if parent_waits else 'time.sleep(.2)'
    job = WindowsJob()
    try:
        process = job.start([sys.executable, '-c', parent], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 8
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert pidfile.exists()
        if parent_waits:
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=.2)
            job.terminate()
        process.communicate(timeout=5)
        assert job.close()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
        kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [W.HANDLE, W.DWORD], W.DWORD
        kernel.CloseHandle.argtypes = [W.HANDLE]
        for pid in json.loads(pidfile.read_text()):
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 0) == 0
                finally:
                    kernel.CloseHandle(handle)
    finally:
        job.close()


@pytest.mark.parametrize('flag, expected', [(True, True), (False, False), (None, False), ('yes', False)])
def test_evidence_binding_flag_fails_closed(tmp_path, flag, expected):
    """R-01 (packet RF-07): only a literal True may mark evidence as bound."""
    from app.runner import code_version
    from app.workflow_v3 import WorkflowRun
    (tmp_path / 'source.py').write_text('pass\n', encoding='utf-8')
    result = {'phase': 'final_check', 'verification_status': 'passed', 'failure_class': 'passed',
              'test_suite_version': 'suite'}
    if flag is not None:
        result['evidence_matches_current_version'] = flag
    ctx = object.__new__(WorkflowRun)
    ctx.boundary = lambda: None
    ctx.source = 'source.py'
    ctx.version = code_version(tmp_path, ('source.py',))
    ctx.tool_calls = 0
    ctx.latest_evidence = ['test-development']
    ctx.final = {'rounds_used': 1, 'test_suite_version': 'suite'}
    ctx.directory = tmp_path
    ctx.deadline = time.monotonic() + 30
    ctx.run_id = 'synthetic-run'
    ctx.runner = SimpleNamespace(run=lambda work, folder, *, phase, timeout, expected_version: dict(result, code_version=expected_version))
    ctx.trace = SimpleNamespace(start=lambda *a, **k: {'step_id': 'synthetic'},
                               evidence_ids=[], complete=lambda **k: None)
    ctx.store = SimpleNamespace(event=lambda *a, **k: None)
    checked, _ = ctx.test(tmp_path, 'final_check', 'final-check')
    assert checked['verification_status'] == 'passed'
    assert ctx.final['evidence_matches_current_version'] is expected


def test_evidence_binding_rejects_candidate_self_mutation(tmp_path):
    """R-01 (packet RF-07): a candidate that rewrites its own shipped copy is not bound."""
    from app.runner import code_version
    from app.split_runner import evidence_binding
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'source.py').write_bytes(b'original\r\n')
    raw = (work / 'source.py').read_bytes()
    shipped = tmp_path / 'shipped.py'
    shipped.write_bytes(raw)
    version = code_version(work, ('source.py',))
    arguments = (shipped, work, ('source.py',), version)
    assert evidence_binding(raw, *arguments) is True
    shipped.write_bytes(b'tampered\r\n')
    assert evidence_binding(raw, *arguments) is False
    shipped.unlink()
    assert evidence_binding(raw, *arguments) is False
    shipped.write_bytes(raw)
    (work / 'source.py').write_bytes(b'changed elsewhere\r\n')
    assert evidence_binding(raw, *arguments) is False
