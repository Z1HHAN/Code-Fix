"""Second-round review regressions; public/candidate code is never executed."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import candidates, cli, public_baseline
from app.candidates import apply_candidate
from app.knowledge import search_knowledge
from app.run_store import RunStore
from app.split_runner import FUNCTIONS, MATERIALIZE_AS_LIST
from app.tasks_v3 import RegisteredTask, catalog, get_task

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('project', list(catalog()))
def test_candidate_test_hash_map_covers_each_registered_project(tmp_path, project):
    task = get_task(project)
    base = task.prepare(tmp_path / 'base')
    source = (base / task.source).read_text(encoding='utf-8')
    result = apply_candidate(base, tmp_path / 'candidate',
                             [{'path': task.source, 'new_content': source}],
                             tmp_path / 'patch', project=project)
    expected = {}
    for test_path in task.profile['test_paths']:
        target = base / test_path
        for path in ([target] if target.is_file() else target.rglob('*')):
            if path.is_file():
                expected[path.relative_to(base).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['test_sha256'] == expected and expected
    assert task.source not in expected
    assert json.loads((tmp_path / 'patch/candidate.json').read_text(encoding='utf-8'))['test_sha256'] == expected
    assert result['test_suite_version'] == task.summary()['test_suite_version']


def test_test_directory_selects_registered_descendants_only(tmp_path, monkeypatch):
    original = get_task('leap')
    task = RegisteredTask(original.project, {**original.data,
                          'profile': {**original.profile, 'test_paths': ['tests']}})
    base = task.prepare(tmp_path / 'base')
    monkeypatch.setattr(candidates, 'get_task', lambda project: task)
    result = apply_candidate(base, tmp_path / 'candidate',
                             [{'path': task.source, 'new_content': '# synthetic bytes only\n'}],
                             tmp_path / 'patch')
    assert result['test_sha256'] == {'tests/test_leap.py': task.profile['approved_files']['tests/test_leap.py']}
    assert not hasattr(candidates, 'SOURCE')


def test_public_baseline_instantiates_explicit_split_runner(tmp_path, monkeypatch):
    calls = []
    class Runner:
        def __init__(self, project):
            calls.append(project)
        def preflight(self, path):
            return {'isolation_ready': False, 'reason': 'offline fixture'}
    monkeypatch.setattr(public_baseline, 'SplitDockerRunner', Runner)
    store = RunStore(tmp_path)
    rid = store.create({'project': 'qb-gcd'})['run_id']
    store.claim(rid)
    status, result = public_baseline.run_public_baseline(store, rid)
    assert calls == ['qb-gcd'] and status == 'blocked' and result['tool_calls'] == 0


def test_cli_dispatches_only_explicit_fixture_command(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['codefix', 'decision-fixtures'])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result) == 4
    # Simulate a future registered command whose implementation was forgotten.
    monkeypatch.setattr(cli.argparse.ArgumentParser, 'parse_args',
                        lambda self: SimpleNamespace(command='future-command'))
    def forbidden(*args, **kwargs):
        pytest.fail('unhandled command must not execute decision fixtures')
    monkeypatch.setattr(cli, 'DecisionEngine', forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


@pytest.fixture
def endpoint():
    # Import only the reviewed controller entry; its socket loop has a main guard.
    spec = importlib.util.spec_from_file_location('reviewed_rpc', ROOT / 'docker/candidate_rpc.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('payload', [None, {}, {'function': '__dict__', 'args': [], 'kwargs': {}},
    {'function': 'unknown', 'args': [], 'kwargs': {}}, {'function': [], 'args': [], 'kwargs': {}},
    {'function': 'is_leap', 'args': {}, 'kwargs': {}}, {'function': 'is_leap', 'args': [], 'kwargs': []},
    {'function': 'is_leap', 'args': [], 'kwargs': {1: 2}},
    {'function': 'is_leap', 'args': [], 'kwargs': {}, 'extra': True}])
def test_invalid_rpc_never_attempts_candidate_import(endpoint, monkeypatch, payload):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid request must not reach importlib')
    monkeypatch.setattr(endpoint.importlib.util, 'spec_from_file_location', forbidden)
    with pytest.raises(ValueError, match='unapproved RPC request'):
        endpoint.execute_request(payload, 'not-a-real-source')


@pytest.mark.parametrize('function', sorted(set(FUNCTIONS.values())))
def test_approved_rpc_dispatches_to_offline_stub(endpoint, monkeypatch, function):
    assert endpoint.ALLOWED_FUNCTIONS == frozenset(FUNCTIONS.values())
    imported = []
    def execute(module):
        imported.append(True)
        setattr(module, function, lambda *args, **kwargs: [args, kwargs])
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=execute))
    # Stubs stand in for importlib; no candidate file or code runs on the host.
    monkeypatch.setattr(endpoint.importlib.util, 'spec_from_file_location', lambda *args: spec)
    monkeypatch.setattr(endpoint.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    assert endpoint.execute_request({'function': function, 'args': [3], 'kwargs': {'x': 4}}, 'stub') == [(3,), {'x': 4}]
    assert imported == [True]


def test_candidate_endpoint_materialize_declaration_matches_host(endpoint):
    # Two files declare the adapter because the endpoint is a standalone entry
    # with no app package; they must never drift apart.
    assert endpoint.MATERIALIZE_AS_LIST == MATERIALIZE_AS_LIST
    assert MATERIALIZE_AS_LIST <= frozenset(FUNCTIONS.values())


@pytest.mark.parametrize('function', sorted(MATERIALIZE_AS_LIST))
def test_materialized_entries_return_lists_over_rpc(endpoint, monkeypatch, function):
    # Without the declared adapter a generator return cannot cross json.dumps
    # and the trusted test would see an opaque RPC failure.
    def execute(module):
        setattr(module, function, lambda *args, **kwargs: (item for item in (1, 2)))

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=execute))
    monkeypatch.setattr(endpoint.importlib.util, 'spec_from_file_location', lambda *args: spec)
    monkeypatch.setattr(endpoint.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    assert endpoint.execute_request({'function': function, 'args': [], 'kwargs': {}}, 'stub') == [1, 2]


@pytest.mark.parametrize('function', sorted(MATERIALIZE_AS_LIST))
def test_materialized_entries_reject_non_iterable_returns(endpoint, monkeypatch, function):
    def execute(module):
        setattr(module, function, lambda *args, **kwargs: 5)

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=execute))
    monkeypatch.setattr(endpoint.importlib.util, 'spec_from_file_location', lambda *args: spec)
    monkeypatch.setattr(endpoint.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    with pytest.raises(TypeError):
        endpoint.execute_request({'function': function, 'args': [], 'kwargs': {}}, 'stub')


def test_native_skip_tasks_register_only_the_official_nodeids():
    # Both tasks' upstream tests call pytest.skip on one explicitly slow case.
    # Only those exact ids may be admitted; any other skip must still block.
    expected = {
        'qb-knapsack': 'python_testcases/test_knapsack.py::test_knapsack[input_data9-13549094]',
        'qb-levenshtein': 'python_testcases/test_levenshtein.py::test_levenshtein[input_data3-42]',
    }
    for project, nodeid in expected.items():
        task = get_task(project)
        assert task.data.get('admission_blocker') in (None, '')
        assert set(task.profile['allowed_skips']) == {nodeid}
        assert nodeid in task.profile['expected_nodeids']


def test_adapter_validation_applies_in_memory_skips_to_runner_task():
    # The second validation pass patches admission policy in memory.  The
    # runner re-loads the registry task, so patching a separate get_task()
    # instance would silently leave the production policy in force.
    script = ROOT / 'scripts/validate_quixbugs_correct_adapters.py'
    spec = importlib.util.spec_from_file_location('validate_correct_adapters', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from app.split_runner import SplitDockerRunner
    project = next(name for name in sorted(catalog()) if name.startswith('qb-'))
    detached = get_task(project)
    runner = SplitDockerRunner(project)
    assert runner.task is not detached
    exact = {next(iter(detached.profile['expected_nodeids'])):
             'official upstream native test skip'}
    detached.data['profile']['allowed_skips'] = dict(exact)
    assert runner.task.profile.get('allowed_skips') != exact
    module.apply_in_memory_skips(runner, exact)
    assert runner.task.profile['allowed_skips'] == exact


def test_negative_control_rejects_buggy_source_that_passes_some_cases():
    # QuixBugs bugs are input-specific, so the pinned buggy file legitimately
    # passes some cases; the gate must look at the overall failed verdict.
    script = ROOT / 'scripts/validate_quixbugs_correct_adapters.py'
    spec = importlib.util.spec_from_file_location('validate_negative_controls', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buggy = {'status': 'failed', 'verification_status': 'failed',
             'failure_class': 'code_runtime_error',
             'counts': {'collected': 7, 'passed': 1, 'failed': 6, 'errors': 0, 'skipped': 0}}
    assert module.negative_control_ok(buggy)
    assert not module.negative_control_ok({**buggy, 'status': 'ok',
                                           'verification_status': 'passed',
                                           'failure_class': 'passed',
                                           'counts': {'collected': 7, 'passed': 7,
                                                      'failed': 0, 'errors': 0, 'skipped': 0}})
    assert not module.negative_control_ok({**buggy, 'failure_class': 'environment_error'})
    assert not module.negative_control_ok({**buggy,
                                           'counts': {'collected': 7, 'passed': 7,
                                                      'failed': 0, 'errors': 0, 'skipped': 0}})


def test_knowledge_coverage_frequency_and_stable_ties(tmp_path):
    directory = tmp_path / 'knowledge/demo_project'
    directory.mkdir(parents=True)
    texts = {'generic': '测试 ' * 100, 'both': '闰年 测试',
             'frequent': '闰年 测试 测试', 'tie': '闰年 测试', 'irrelevant': '没有相关词'}
    for name, text in texts.items():
        (directory / f'{name}.md').write_text(text, encoding='utf-8')
    ids = tuple(texts)
    results = search_knowledge(tmp_path, ids, '闰年 测试 测试')
    assert [item.knowledge_id for item in results] == ['frequent', 'both', 'tie', 'generic']
    assert [item.citation_id for item in results] == ['knowledge-' + item.knowledge_id for item in results]
    assert search_knowledge(tmp_path, ids, '') == []
    assert search_knowledge(tmp_path, ('missing', 'irrelevant'), '闰年 测试') == []
