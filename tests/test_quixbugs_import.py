"""Importer checks operate on bytes/AST/JSON; no public program is executed."""
import json
from pathlib import Path

import pytest

from app.quixbugs_import import (BUNDLE, ROOT, import_dataset, load_bundle,
                                 load_skip_approvals, static_test_ids)


def empty_registry(tmp_path):
    path = tmp_path / 'configs/tasks/catalog-v3.json'
    path.parent.mkdir(parents=True)
    original = json.loads((ROOT / 'configs/tasks/catalog-v3.json').read_bytes())
    path.write_text(json.dumps({'schema_version': 1, 'projects': {'leap': original['projects']['leap']}}), encoding='utf-8')
    return path


def test_full_bundle_hash_verification_and_static_id_compatibility():
    files, digest = load_bundle()
    assert len(files) == 116 and len(digest) == 64
    assert not any(path.startswith(('correct_', '.git', 'evaluation')) for path in files)
    current = json.loads((ROOT / 'configs/tasks/catalog-v3.json').read_bytes())
    for name in ['gcd', 'get_factors', 'max_sublist_sum']:
        ids, skip, _ = static_test_ids(name, files)
        assert ids == current['projects']['qb-'+name]['profile']['expected_nodeids']
        assert not skip
    # The shipped registry must match the reviewed approval file exactly.
    approvals = load_skip_approvals(ROOT)
    assert set(approvals) == {'qb-knapsack', 'qb-levenshtein'}
    for project, entries in approvals.items():
        registered = current['projects'][project]
        assert registered['profile']['allowed_skips'] == entries
        assert registered.get('admission_blocker') is None


def test_preview_does_not_write_and_never_starts_a_process(tmp_path, monkeypatch):
    path = empty_registry(tmp_path)
    original = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('data import must not start a subprocess')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    report = import_dataset(root=tmp_path)
    assert report['public_tasks'] == 40 and report['pre_registered_native_test_count'] == 278
    assert report['upstream_files_verified'] == 116 and report['new_asset_directories'] == 40
    assert report['blocked_native_skip_policy'] == ['qb-knapsack', 'qb-levenshtein']
    assert not report['public_code_executed'] and not report['actual_test_collection_performed']
    assert path.read_bytes() == original and not (tmp_path / 'task_assets').exists()


def test_import_is_idempotent_and_every_task_has_only_selected_assets(tmp_path):
    path = empty_registry(tmp_path)
    first = import_dataset(root=tmp_path, write=True)
    original = path.read_bytes()
    second = import_dataset(root=tmp_path, write=True)
    assert first['new_asset_directories'] == 40 and first['catalog_changed']
    assert second['new_asset_directories'] == 0 and not second['catalog_changed']
    assert path.read_bytes() == original
    for project, data in json.loads(original)['projects'].items():
        if project == 'leap':
            continue
        directory = tmp_path / data['profile']['fixture_dir']
        names = {file.relative_to(directory).as_posix() for file in directory.rglob('*') if file.is_file()}
        assert names == set(data['profile']['approved_files'])
        assert 'LICENSE' in names
        assert not any(name.startswith(('correct_', '.git', 'evaluation')) for name in names)
    graph = json.loads(original)['projects']['qb-breadth_first_search']
    assert graph['context_paths'] == ['python_testcases/node.py']


@pytest.mark.parametrize('fault', ['changed_bytes', 'changed_pin', 'missing', 'duplicate', 'reference', 'invalid_type'])
def test_import_rejects_invalid_bundle_before_any_write(tmp_path, fault):
    path = empty_registry(tmp_path)
    original = path.read_bytes()
    bundle = json.loads(BUNDLE.read_bytes())
    if fault == 'changed_bytes':
        bundle['files'][0]['content'] += '\nchanged'
    elif fault == 'changed_pin':
        bundle['commit'] = '0'*40
    elif fault == 'missing':
        bundle['files'].pop()
    elif fault == 'duplicate':
        bundle['files'].append(bundle['files'][0])
    elif fault == 'reference':
        bundle['files'].append({'path': 'correct_python_programs/gcd.py', 'content': 'secret'})
    else:
        bundle['files'][0]['content'] = []
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(bundle), encoding='utf-8')
    with pytest.raises(ValueError):
        import_dataset(source_bundle=bad, root=tmp_path, write=True)
    assert path.read_bytes() == original and not (tmp_path / 'task_assets').exists()


def test_import_never_overwrites_changed_existing_task(tmp_path):
    path = empty_registry(tmp_path)
    directory = tmp_path / 'task_assets/quixbugs/gcd'
    directory.mkdir(parents=True)
    existing = directory / 'changed.txt'
    existing.write_text('keep this')
    with pytest.raises(ValueError, match='拒绝覆盖'):
        import_dataset(root=tmp_path, write=True)
    assert existing.read_text() == 'keep this'
    assert list(directory.parent.iterdir()) == [directory]
    assert list(json.loads(path.read_bytes())['projects']) == ['leap']


def test_catalog_write_failure_leaves_complete_assets_and_can_retry(tmp_path, monkeypatch):
    import app.quixbugs_import as module
    path = empty_registry(tmp_path)
    original = path.read_bytes()
    replace = module.os.replace
    def fail(*args):
        raise OSError('injected registry write failure')
    monkeypatch.setattr(module.os, 'replace', fail)
    with pytest.raises(OSError):
        import_dataset(root=tmp_path, write=True)
    assert path.read_bytes() == original
    monkeypatch.setattr(module.os, 'replace', replace)
    report = import_dataset(root=tmp_path, write=True)
    assert report['new_asset_directories'] == 0
    assert len(json.loads(path.read_bytes())['projects']) == 41


def test_static_analysis_does_not_run_top_level_code():
    files, _ = load_bundle()
    files['python_testcases/test_gcd.py'] = b'raise RuntimeError("must never execute")\n' + files['python_testcases/test_gcd.py']
    ids, _, _ = static_test_ids('gcd', files)
    assert len(ids) == 6


@pytest.mark.parametrize('persistent', [False, True])
def test_publish_permission_retries_have_exact_backoff(monkeypatch, persistent):
    import app.quixbugs_import as module
    from types import SimpleNamespace
    attempts, waits = [], []
    def operation(source, destination):
        attempts.append((source, destination))
        if persistent or len(attempts) == 1:
            raise PermissionError('injected denial; no filesystem involved')
        return 'published'
    monkeypatch.setattr(module, 'time', SimpleNamespace(sleep=waits.append))
    if persistent:
        with pytest.raises(PermissionError):
            module._publish(operation, 'source', 'destination')
        assert attempts == [('source', 'destination')] * 6
        assert waits == [0.05, 0.1, 0.2, 0.4, 0.8]
    else:
        assert module._publish(operation, 'source', 'destination') == 'published'
        assert attempts == [('source', 'destination')] * 2
        assert waits == [0.05]


@pytest.mark.parametrize('persistent', [False, True])
def test_windows_permission_failure_is_bounded_and_registry_remains_safe(tmp_path, monkeypatch, persistent):
    import app.quixbugs_import as module
    from types import SimpleNamespace
    path = empty_registry(tmp_path)
    original = path.read_bytes()
    rename = Path.rename
    attempts = []
    waits = []
    published = []
    def denied(source, destination):
        if not source.is_relative_to(tmp_path):
            return rename(source, destination)
        attempts.append(source)
        if persistent or len(attempts) == 1:
            raise PermissionError('injected file move denial')
        result = rename(source, destination)
        published.append(Path(destination))
        return result
    monkeypatch.setattr(module.Path, 'rename', denied)
    # Use real backoff for real Windows moves. Exact retry counts are tested
    # above with a deterministic operation; OS locks can add permission retries.
    sleep = module.time.sleep
    def wait(seconds):
        waits.append(seconds)
        sleep(seconds)
    monkeypatch.setattr(module, 'time', SimpleNamespace(sleep=wait))
    if persistent:
        with pytest.raises(PermissionError):
            import_dataset(root=tmp_path, write=True)
        assert len(attempts) == 6 and len(waits) == 5
        assert path.read_bytes() == original
        monkeypatch.setattr(module.Path, 'rename', rename)
        assert import_dataset(root=tmp_path, write=True)['public_tasks'] == 40
    else:
        assert import_dataset(root=tmp_path, write=True)['public_tasks'] == 40
        assert len(published) == len(set(published)) == 40
        assert len(attempts) == len(published) + len(waits)
        assert 1 <= len(waits) <= 5 * 40
        assert set(waits) <= {0.05, 0.1, 0.2, 0.4, 0.8}
    assert len(json.loads(path.read_bytes())['projects']) == 41


def test_approved_native_skips_are_registered_exactly(tmp_path):
    path = empty_registry(tmp_path)
    approvals_path = tmp_path / 'configs/tasks/native-skip-approvals.json'
    approvals_path.write_text(
        (ROOT / 'configs/tasks/native-skip-approvals.json').read_text(encoding='utf-8'),
        encoding='utf-8')
    report = import_dataset(root=tmp_path, write=True)
    assert report['blocked_native_skip_policy'] == []
    catalog = json.loads(path.read_bytes())['projects']
    approvals = load_skip_approvals(tmp_path)
    for project, entries in approvals.items():
        assert catalog[project]['admission_blocker'] is None
        assert catalog[project]['profile']['allowed_skips'] == entries
        assert set(entries) <= set(catalog[project]['profile']['expected_nodeids'])


def test_unknown_native_skip_approval_rejects_import_before_write(tmp_path):
    path = empty_registry(tmp_path)
    original = path.read_bytes()
    approvals_path = tmp_path / 'configs/tasks/native-skip-approvals.json'
    approvals_path.write_text(json.dumps({'schema_version': 1, 'approvals': {
        'qb-knapsack': {'python_testcases/test_knapsack.py::test_knapsack[not-real]': 'x'}}}),
        encoding='utf-8')
    with pytest.raises(ValueError, match='skip nodeid'):
        import_dataset(root=tmp_path, write=True)
    assert path.read_bytes() == original and not (tmp_path / 'task_assets').exists()


@pytest.mark.parametrize('mode', ['baseline', 'repair'])
def test_native_skip_task_blocks_before_any_probe_or_model(tmp_path, monkeypatch, mode):
    # A fresh import without the approvals file must keep the fail-closed
    # policy: the static scan flags the slow native skip and admission blocks
    # before any probe or model call.
    from app.service import TaskService
    empty_registry(tmp_path)
    import_dataset(root=tmp_path, write=True)
    monkeypatch.setattr('app.tasks_v3.ROOT', tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('unqualified native skip policy must block before probe/model')
    monkeypatch.setattr('app.docker_runner.DockerRunner.preflight', forbidden)
    monkeypatch.setattr('app.live_model.LiveModel.call', forbidden)
    service = TaskService(tmp_path / 'jobs')
    rid = service.store.create({'project': 'qb-knapsack', 'mode': mode})['run_id']
    result = service.execute(rid)
    assert result['status'] == 'blocked' and '慢测' in result['result']['reason']
    assert result['result']['model_calls'] == result['result']['tool_calls'] == 0
