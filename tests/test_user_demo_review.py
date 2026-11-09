"""User-facing readiness, archive and display metadata contracts; no paid calls."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.configuration import ConfigurationStore
from app.errors import RunNotFound
from app.run_store import RunStore, StateConflict


def test_archive_migrates_existing_store_and_keeps_evidence_and_status(tmp_path):
    store = RunStore(tmp_path)
    run = store.create({}, 'original-key')
    rid = run['run_id']
    store.claim(rid)
    store.event(rid, {'event': 'agent_call', 'cost_cny': '0.01', 'raw_text': 'historical-body'})
    store.finish(rid, 'failed', {'reason': 'original failure'})
    artifact = store.directory(rid) / 'original.txt'
    artifact.write_bytes(b'original\r\n')
    # Simulate a pre-feature database, preserving its existing rows and schema.
    with store.connect() as db:
        db.execute('DROP TABLE run_archives')
    store = RunStore(tmp_path)
    original = store.snapshot(rid)
    archived = store.set_archived(rid, True)
    assert archived['archived_at'] and archived['status'] == 'failed'
    assert archived['result'] == original['run']['result']
    assert store.list() == []
    assert store.list(include_archived=True)[0]['run_id'] == rid
    assert store.create({}, 'original-key')['archived_at'] == archived['archived_at']
    assert store.snapshot(rid)['events'][:-1] == original['events']
    assert artifact.read_bytes() == b'original\r\n'
    assert store.set_archived(rid, True)['archived_at'] == archived['archived_at']
    assert len(store.snapshot(rid)['events']) == len(original['events']) + 1
    restored = store.set_archived(rid, False)
    assert restored['archived_at'] is None and restored['status'] == 'failed'
    store.set_archived(rid, False)
    assert len(store.snapshot(rid)['events']) == len(original['events']) + 2
    assert store.list()[0]['run_id'] == rid
    assert artifact.read_bytes() == b'original\r\n'


def test_running_archive_rejected_and_created_requires_restore_before_claim(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({})['run_id']
    store.set_archived(rid, True)
    with pytest.raises(StateConflict):
        store.claim(rid)
    store.set_archived(rid, False)
    assert store.get(rid)['status'] == 'created'
    store.claim(rid)
    with pytest.raises(StateConflict):
        store.set_archived(rid, True)
    assert store.get(rid)['archived_at'] is None
    with pytest.raises(RunNotFound):
        store.set_archived('run-' + 'f' * 32, False)


def test_claim_and_archive_are_atomic_against_each_other(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({})['run_id']
    barrier = Barrier(2)
    def operate(archive):
        barrier.wait(timeout=5)
        try:
            store.set_archived(rid, True) if archive else store.claim(rid)
            return True
        except StateConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(operate, [False, True]))
    assert sum(results) == 1
    run = store.get(rid)
    assert (run['status'] == 'running' and run['archived_at'] is None) or (
        run['status'] == 'created' and run['archived_at'])


def test_archive_api_auth_restore_and_reports_remain_readable(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        token = {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}
        rid = client.post('/api/v3/tasks', json={}, headers=token).json()['run_id']
        store = RunStore(tmp_path)
        store.claim(rid)
        assert client.post(f'/api/v3/tasks/{rid}/archive', headers=token).status_code == 409
        store.finish(rid, 'failed', {'reason': 'fixture'})
        artifact = store.directory(rid) / 'original.txt'
        artifact.write_bytes(b'unchanged\r\n')
        assert client.post(f'/api/v3/tasks/{rid}/archive').status_code == 403
        assert client.post(f'/api/v3/tasks/{rid}/archive', headers=token).status_code == 200
        assert client.get('/api/v3/tasks').json() == []
        assert len(client.get('/api/v3/tasks?include_archived=true').json()) == 1
        assert client.get(f'/api/v3/tasks/{rid}/report').status_code == 200
        assert client.get(f'/api/v3/tasks/{rid}/bundle').status_code == 200
        assert artifact.read_bytes() == b'unchanged\r\n'
        assert client.post(f'/api/v3/tasks/{rid}/start', headers=token).status_code == 409
        assert client.post(f'/api/v3/tasks/{rid}/restore', headers=token).json()['archived_at'] is None
        assert store.get(rid)['status'] == 'failed'
        assert len(client.get('/api/v3/tasks').json()) == 1


def test_display_names_are_catalog_metadata_not_saved_runtime_identity(tmp_path, monkeypatch):
    import app.configuration as module
    store = RunStore(tmp_path / 'store')
    configs = ConfigurationStore(store)
    saved = configs.save({'bindings': {role: 'sf_account1_v32' for role in ('repairer', 'detector', 'scheduler', 'locator')},
                          'parameters': {'max_rounds': 2, 'max_calls': 9, 'total_seconds': 120}, 'strategy': 'dynamic'})
    original = module.load_profiles
    registry = original(configs.root)
    registry['accounts']['another_account'] = dict(registry['accounts']['sf_account1'])
    monkeypatch.setattr(module, 'load_profiles', lambda root: registry)
    # Optional names absent on old deployments still produce readable defaults.
    real_read = module.read_json
    monkeypatch.setattr(module, 'read_json', lambda path: {'schema_version': 1, 'labels': {'sf_account1': '演示账号'}} if path.name == 'account_labels.json' else real_read(path))
    names = {a['account_id']: a['display_name'] for a in configs.catalog()['accounts']}
    assert names['sf_account1'] == '演示账号'
    assert names['another_account'] == '账号3'
    monkeypatch.setattr(module, 'load_profiles', original)
    assert configs.runtime(saved['config_id'])
    assert configs.get(saved['config_id']) == saved
    assert store.create({'mode': 'repair'})['task'].get('config_id') is None


def test_cli_archive_success_even_for_failed_task_and_restore_never_starts(tmp_path, monkeypatch, capsys):
    from app.cli import main
    monkeypatch.setenv('CODE_FIX_RUN_ROOT', str(tmp_path))
    store = RunStore(tmp_path)
    rid = store.create({})['run_id']
    store.claim(rid)
    store.finish(rid, 'failed', {'reason': 'fixture'})
    for command in ['archive-task', 'restore-task']:
        monkeypatch.setattr(sys, 'argv', ['codefix', command, rid])
        assert main() == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'failed'
    assert store.get(rid)['status'] == 'failed'


def test_cli_help_explains_import_and_archive_and_serve_returns_normally(monkeypatch, capsys):
    from app.cli import main
    import uvicorn
    import app.api
    monkeypatch.setattr(sys, 'argv', ['codefix', '--help'])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert '预览QuixBugs资产导入' in text and '归档非运行任务' in text
    monkeypatch.setattr(app.api, 'create_app', lambda: 'fixture-app')
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sys, 'argv', ['codefix', 'serve'])
    assert main() == 0
    assert calls[0][1]['host'] == '127.0.0.1'
