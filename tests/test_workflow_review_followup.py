"""E-07/09/10: retain byte binding while exposing only safe diagnostics."""
import json
import time
from types import SimpleNamespace

import pytest

import app.workflow_v3 as workflow
from app.runner import code_version
from app.tasks_v3 import get_task


@pytest.mark.parametrize('phase', ['development', 'final_check'])
@pytest.mark.parametrize('mutate', [False, True])
def test_execution_remains_bound_to_bytes_at_dispatch(tmp_path, phase, mutate):
    source = tmp_path / 'source.py'
    source.write_bytes(b'pass\r\n')
    version = code_version(tmp_path, ('source.py',))
    ctx = object.__new__(workflow.WorkflowRun)
    ctx.boundary = lambda: None
    ctx.source = 'source.py'
    ctx.version = version if phase == 'final_check' else 'unrelated-previous-version'
    ctx.tool_calls = 0
    ctx.latest_evidence = []
    ctx.final = {'rounds_used': 0, 'test_suite_version': 'suite'}
    ctx.directory, ctx.run_id = tmp_path, 'synthetic'
    ctx.deadline = time.monotonic() + 30
    completed = []
    ctx.trace = SimpleNamespace(start=lambda *a, **k: {}, evidence_ids=[],
                                complete=lambda **k: completed.append(k))
    ctx.store = SimpleNamespace(event=lambda *a, **k: None)

    def run(work, folder, *, phase, timeout, expected_version):
        assert expected_version == version
        if mutate:
            source.write_bytes(b'changed\r\n')
        return {'phase': phase, 'code_version': version, 'test_suite_version': 'suite',
                'verification_status': 'passed', 'failure_class': 'passed',
                'evidence_matches_current_version': True}

    ctx.runner = SimpleNamespace(run=run)
    if mutate:
        with pytest.raises(workflow.IsolationBlocked, match='执行后证据版本不一致'):
            ctx.test(tmp_path, phase, 'check')
        assert not completed
        assert not ctx.final.get('evidence_matches_current_version', False)
    else:
        result, _ = ctx.test(tmp_path, phase, 'check')
        assert result['verification_status'] == 'passed'
        assert ctx.final['evidence_matches_current_version'] is True


@pytest.mark.parametrize('corrupt', ['bytes', 'record'])
def test_final_replay_rejects_corrupted_copy_or_record(tmp_path, monkeypatch, corrupt):
    definition = get_task('leap')
    ctx = object.__new__(workflow.WorkflowRun)
    ctx.base = definition.prepare(tmp_path / 'base')
    ctx.current, ctx.source = ctx.base, definition.source
    ctx.directory = tmp_path / 'run'
    ctx.task = {'project': 'leap'}
    ctx.version = code_version(ctx.current, (ctx.source,))
    apply = workflow.apply_candidate

    def replay(base, destination, *args, **kwargs):
        record = apply(base, destination, *args, **kwargs)
        if corrupt == 'bytes':
            (destination / ctx.source).write_bytes(b'changed\r\n')
        else:
            record['candidate_version'] = 'wrong'
        return record

    monkeypatch.setattr(workflow, 'apply_candidate', replay)
    ctx.test = lambda *a, **k: pytest.fail('corrupted replay reached final runner')
    with pytest.raises(workflow.IsolationBlocked, match='最终验收字节与候选版本不一致'):
        ctx.final_check()


@pytest.mark.parametrize('stream', ['stdout_path', 'stderr_path'])
@pytest.mark.parametrize('prefix', ['FAILED', 'ERROR'])
@pytest.mark.parametrize('detail,exception,module', [
    ("ImportError: No module named 'numpy'", 'ImportError', 'numpy'),
    ("ModuleNotFoundError: No module named 'numpy.linalg'", 'ModuleNotFoundError', 'numpy.linalg'),
    ('TypeError: /private/SECRET value=999', 'TypeError', None),
    ("ImportError: No module named '/private/SECRET'", 'ImportError', None),
    ('AssertionError: assert SECRET == 999', 'AssertionError', None),
    ('TokenValueError: SECRET', None, None),
    ('assert SECRET == 999', None, None),
])
def test_summary_diagnostics_keep_only_known_types_and_module_identifiers(
        tmp_path, stream, prefix, detail, exception, module):
    raw = f'{prefix} tests/t.py::test_x[SECRET] - {detail}\n'
    log = tmp_path / 'log.txt'
    log.write_text(raw, encoding='utf-8')
    result = {stream: 'log.txt', 'failure_class': 'environment_error', 'code_version': 'synthetic'}
    summary = json.loads(workflow.WorkflowRun.evidence(result, tmp_path)['summary'])
    assert 'SECRET' not in json.dumps(summary)
    assert '999' not in json.dumps(summary)
    assert '/private' not in json.dumps(summary)
    excerpt = summary['stdout' if stream == 'stdout_path' else 'stderr']
    assert f'{prefix} tests/t.py::test_x' in excerpt
    if exception and exception != 'AssertionError':
        assert excerpt.endswith(' - ' + exception)
    else:
        assert detail not in excerpt
    assert summary['diagnostics'] == (
        {'exception_types': [exception], 'missing_modules': [module] if module else []}
        if exception else None)
    assert log.read_text(encoding='utf-8') == raw
