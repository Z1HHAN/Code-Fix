"""Public assets are only read/copied. No public Python is imported or executed."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.candidates import apply_candidate
from app.service import TaskService
from app.tasks_v3 import get_task, list_tasks

PUBLIC = ['qb-get_factors', 'qb-gcd', 'qb-max_sublist_sum']


def test_catalog_records_native_counts_and_pinned_provenance():
    tasks = list_tasks()
    assert [task['expected_test_count'] for task in tasks] == [4, 11, 6, 6]
    assert [task['project'] for task in tasks] == ['leap', *PUBLIC]
    for task in tasks[1:]:
        assert task['requires_isolation'] is True
        assert task['source']['commit'] == '4257f44b0ff1181dedaedee6a447e133219fcebf'
        assert task['source']['upstream_redownloaded'] is False


@pytest.mark.parametrize('project', PUBLIC)
def test_prepare_verifies_exact_allowlist_and_rejects_modified_native_data(tmp_path, project):
    task = get_task(project)
    work = task.prepare(tmp_path / 'work')
    assert len([p for p in work.rglob('*') if p.is_file()]) == 6
    assert not any(p.name in {'correct_python_programs', '.git', 'evaluation'} for p in work.rglob('*'))
    with pytest.raises(FileExistsError):
        task.prepare(work)
    test = work / task.profile['test_paths'][0]
    test.write_bytes(test.read_bytes() + b'\n# altered\n')
    with pytest.raises(ValueError, match='哈希'):
        apply_candidate(work, tmp_path / 'candidate', [{'path': task.source, 'new_content': 'x=1\n'}], tmp_path / 'patch', project=project)
    assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('project', PUBLIC)
def test_public_baseline_without_docker_never_uses_host_or_model(tmp_path, monkeypatch, project):
    monkeypatch.delenv('CODE_FIX_EXECUTION_APPROVED', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('public baseline must not execute host code or call a model')
    monkeypatch.setattr('app.service.baseline_record', forbidden)
    monkeypatch.setattr('app.live_model.LiveModel.call', forbidden)
    service = TaskService(tmp_path)
    run = service.store.create({'project': project, 'mode': 'baseline'})
    finished = service.execute(run['run_id'])
    assert finished['status'] == 'blocked'
    assert finished['result']['tool_calls'] == 0
    assert finished['result']['model_api_called'] is False
    assert finished['result']['final_result'] == 'not_run'
    assert not (service.store.directory(run['run_id']) / 'base').exists()
    assert '隔离' in finished['result']['reason']


@pytest.mark.parametrize('project', PUBLIC)
def test_candidate_cannot_cross_into_other_tasks_or_test_paths(tmp_path, project):
    task = get_task(project)
    work = task.prepare(tmp_path / 'work')
    for wrong in ['code/leap_year.py', task.profile['test_paths'][0], 'conftest.py', 'correct_python_programs/answer.py']:
        with pytest.raises(ValueError):
            apply_candidate(work, tmp_path / 'candidate', [{'path': wrong, 'new_content': ''}], tmp_path / 'patch', project=project)


def test_http_catalog_task_selection_and_unknown_project_rejection(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        tasks = client.get('/api/v3/projects').json()
        assert len(tasks) == 4
        token = client.get('/api/v3/session').json()['token']
        headers = {'X-Codefix-Token': token}
        for task in tasks:
            response = client.post('/api/v3/tasks', headers=headers, json={'project': task['project']})
            assert response.status_code == 201
            assert response.json()['task']['description'] == task['description']
        for unknown in ['../../answer', 'all', [], None]:
            assert client.post('/api/v3/tasks', headers=headers, json={'project': unknown}).status_code == 422


def test_cli_selects_public_project_and_preserves_budget_flags(tmp_path):
    env = os.environ.copy()
    env['CODE_FIX_RUN_ROOT'] = str(tmp_path)
    output = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'app.cli', 'create-task', '--project', 'qb-gcd',
                             '--max-calls', '2', '--total-seconds', '15'], env=env, capture_output=True, check=True, timeout=10)
    task = json.loads(output.stdout)['task']
    assert task['project'] == 'qb-gcd' and task['max_calls'] == 2 and task['total_seconds'] == 15
    assert 'gcd' in task['description'] and '闰年' not in task['description']
