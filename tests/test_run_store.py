from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.run_store import RunStore, StateConflict, validate_task
from app.service import TaskService


def test_atomic_idempotency_and_claim_across_connections(tmp_path):
    store = RunStore(tmp_path)
    def create(_):
        return RunStore(tmp_path).create({}, 'same')['run_id']
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    def claim(_):
        try:
            RunStore(tmp_path).claim(ids[0])
            return True
        except StateConflict:
            return False
    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(claim, range(8))) == 1
    with pytest.raises(StateConflict):
        store.create({'max_rounds': 1}, 'same')


@pytest.mark.parametrize('task', [{'mode': 'shell'}, {'project': '../reference'}, {'max_rounds': True},
                                 {'max_rounds': 6}, {'command': 'pytest'}, {'description': ''}])
def test_task_rejects_unregistered_inputs(task):
    with pytest.raises(ValueError):
        validate_task(task)


def test_real_baseline_survives_new_service_and_has_report(tmp_path):
    service = TaskService(tmp_path)
    run_id = service.store.create({})['run_id']
    run = service.execute(run_id)
    assert run['status'] == 'stopped'
    assert run['result']['tool_result']['counts']['failed'] == 1
    assert run['result']['final_result'] == 'not_run'
    restored = TaskService(tmp_path)
    assert restored.store.get(run_id) == run
    assert restored.write_report(run_id).is_file()
    with pytest.raises(StateConflict):
        restored.execute(run_id)


@pytest.mark.parametrize('operation', ['claim', 'finish', 'control'])
def test_state_and_event_roll_back_together_if_event_write_fails(tmp_path, operation):
    store = RunStore(tmp_path)
    rid = store.create({})['run_id']
    if operation != 'claim':
        store.claim(rid)
    before = store.snapshot(rid)
    # Simulate evidence persistence failure inside SQLite, after UPDATE.
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_events BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'disk fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        if operation == 'claim':
            store.claim(rid)
        elif operation == 'finish':
            store.finish(rid, 'stopped', {'final_result': 'not_run'})
        else:
            store.request_control(rid, 'stop')
    assert store.snapshot(rid) == before


def test_directory_failure_does_not_leave_task_running(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({})['run_id']
    store.directory(rid).write_text('not a directory')
    with pytest.raises(FileExistsError):
        store.claim(rid)
    assert store.get(rid)['status'] == 'created'
    assert store.events(rid) == []


def test_stop_cannot_be_downgraded_to_pause(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({})['run_id']
    store.claim(rid)
    for action in ['pause', 'stop', 'pause']:
        store.request_control(rid, action)
    assert store.get(rid)['control'] == 'stop'
    assert store.events(rid)[-1]['effective_action'] == 'stop'
    store.finish(rid, 'stopped', {'final_result': 'not_run'})
    with pytest.raises(StateConflict):
        store.request_control(rid, 'pause')


@pytest.mark.parametrize('action, expected', [('pause', 'paused'), ('stop', 'stopped')])
def test_queued_control_skips_baseline_execution(tmp_path, monkeypatch, action, expected):
    service = TaskService(tmp_path)
    rid = service.store.create({})['run_id']
    service.store.claim(rid)
    service.store.request_control(rid, action)
    def forbidden(*args, **kwargs):
        pytest.fail('queued task must not start a test after control')
    monkeypatch.setattr('app.service.baseline_record', forbidden)
    service.run_claimed(rid)
    report = service.report(rid)
    assert report['run']['status'] == expected
    assert report['run']['result']['tool_calls'] == 0
    assert report['events'][-1]['status'] == expected
