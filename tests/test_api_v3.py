import hashlib
import io
import json
import time
from threading import Event
import zipfile

from fastapi.testclient import TestClient
import pytest

from app.api import create_app


def auth(client):
    return {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}


def test_real_baseline_http_status_evidence_and_export(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        headers = auth(client) | {'Idempotency-Key': 'api-baseline'}
        created = client.post('/api/v3/tasks', headers=headers, json={'mode': 'baseline'})
        assert created.status_code == 201
        rid = created.json()['run_id']
        assert client.post('/api/v3/tasks', headers=headers, json={'mode': 'baseline'}).json()['run_id'] == rid
        assert client.post(f'/api/v3/tasks/{rid}/start', headers=headers).status_code == 202
        assert client.post(f'/api/v3/tasks/{rid}/start', headers=headers).status_code == 409
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = client.get(f'/api/v3/tasks/{rid}').json()
            if run['status'] != 'running':
                break
            time.sleep(.05)
        assert run['status'] == 'stopped'
        assert run['result']['tool_result']['counts']['passed'] == 3
        assert run['result']['tool_result']['counts']['failed'] == 1
        assert 'test_1900_is_not_leap' in run['result']['stdout_excerpt']
        assert run['result']['final_result'] == 'not_run'
        assert client.get('/').status_code == 200
        assert client.get('/assets/app.js').status_code == 200
        exported = client.get(f'/api/v3/tasks/{rid}/bundle')
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read('sha256.json'))
            assert any(name.endswith('junit.xml') for name in manifest)
            assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest.items())
            assert json.loads(archive.read('report.json'))['run']['status'] == 'stopped'


def test_http_repair_blocks_without_fake_success(tmp_path, monkeypatch):
    monkeypatch.delenv('CODE_FIX_LIVE_APPROVED', raising=False)
    monkeypatch.delenv('CODE_FIX_EXECUTION_APPROVED', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        headers = auth(client)
        rid = client.post('/api/v3/tasks', headers=headers, json={'mode': 'repair'}).json()['run_id']
        client.post(f'/api/v3/tasks/{rid}/start', headers=headers)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            run = client.get(f'/api/v3/tasks/{rid}').json()
            if run['status'] == 'blocked':
                break
            time.sleep(.03)
        assert run['status'] == 'blocked'
        assert run['result']['model_calls'] == 0
        assert run['result']['final_result'] == 'not_run'
        assert run['result']['blockers']


def test_request_boundary_and_validation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        headers = auth(client)
        assert client.post('/api/v3/tasks', json={}).status_code == 403
        assert client.post('/api/v3/tasks', headers=headers | {'Origin': 'https://foreign.invalid'}, json={}).status_code == 403
        assert client.get('/api/v3/tasks', headers={'Host': 'foreign.invalid'}).status_code == 403
        assert client.post('/api/v3/tasks', headers=headers, json={'command': 'cmd.exe'}).status_code == 422
        assert client.post('/api/v3/tasks', headers=headers, content='not json').status_code == 422
        assert client.post('/api/v3/tasks', headers=headers, content='x'*8193).status_code == 413
        assert client.get('/api/v3/tasks/run-'+'a'*32).status_code == 404
        for invalid_mode in ([], {}, None):
            assert client.post('/api/v3/tasks', headers=headers, json={'mode': invalid_mode}).status_code == 422
        rid = client.post('/api/v3/tasks', headers=headers, json={}).json()['run_id']
        assert client.post(f'/api/v3/tasks/{rid}/control', headers=headers, json={'action': []}).status_code == 422
        assert client.post('/api/v3/tasks', headers=headers, content=iter([b'x'*4096, b'x'*4097])).status_code == 413


def test_artifact_paths_cannot_escape_and_open_runs_cannot_export(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        rid = client.post('/api/v3/tasks', headers=auth(client), json={}).json()['run_id']
        assert client.get(f'/api/v3/tasks/{rid}/artifact', params={'path': '../tasks.sqlite3'}).status_code == 422
        assert client.get(f'/api/v3/tasks/{rid}/bundle').status_code == 409


def test_selected_run_is_persisted_across_app_instances(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        run = client.post('/api/v3/tasks', headers=auth(client), json={}).json()
    with TestClient(create_app(tmp_path)) as restored:
        assert restored.get(f'/api/v3/tasks/{run["run_id"]}').json()['task'] == run['task']


@pytest.mark.parametrize('action, expected', [('pause', 'paused'), ('stop', 'stopped')])
def test_control_while_real_baseline_runs_preserves_evidence(tmp_path, monkeypatch, action, expected):
    from app.service import baseline_record
    entered, release = Event(), Event()
    def gated_baseline(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'test did not release baseline worker'
        # Only the scheduling boundary is gated: this executes the real frozen pytest.
        return baseline_record(*args, **kwargs)
    monkeypatch.setattr('app.service.baseline_record', gated_baseline)
    with TestClient(create_app(tmp_path)) as client:
        headers = auth(client)
        rid = client.post('/api/v3/tasks', headers=headers, json={'mode': 'baseline'}).json()['run_id']
        try:
            assert client.post(f'/api/v3/tasks/{rid}/start', headers=headers).status_code == 202
            assert entered.wait(3)
            response = client.post(f'/api/v3/tasks/{rid}/control', headers=headers, json={'action': action})
            assert response.status_code == 200
            assert response.json()['control'] == action
        finally:
            release.set()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            report = client.get(f'/api/v3/tasks/{rid}/report').json()
            if report['run']['status'] != 'running':
                break
            time.sleep(.03)
        assert report['run']['status'] == expected
        assert report['run']['result']['tool_result']['counts']['passed'] == 3
        assert report['run']['result']['tool_result']['counts']['failed'] == 1
        assert report['run']['result']['model_api_called'] is False
        assert report['run']['result']['final_result'] == 'not_run'
        assert [e['event'] for e in report['events']] == ['started', 'control_requested', 'finished']
        assert report['events'][-1]['status'] == expected
        exported = client.get(f'/api/v3/tasks/{rid}/bundle')
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert json.loads(archive.read('report.json'))['run']['status'] == expected
            manifest = json.loads(archive.read('sha256.json'))
            assert any(name.endswith('junit.xml') for name in manifest)
            assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest.items())
