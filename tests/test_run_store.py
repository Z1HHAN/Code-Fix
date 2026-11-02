from concurrent.futures import ThreadPoolExecutor

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
                                 {'max_rounds': 4}, {'command': 'pytest'}, {'description': ''}])
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
