import io
import json
import zipfile

from fastapi.testclient import TestClient
from app.api import create_app
from app.service import TaskService
from test_chain_retries import execute
from test_workflow_v3 import ModelStub


def test_result_summary_references_actual_tests_and_original_final_code(tmp_path):
    status, result, runner, store, rid = execute(tmp_path, ModelStub(), ['assertion_failed', 'passed', 'passed'])
    store.finish_if_running(rid, status, result)
    summary = TaskService(tmp_path).report(rid)['result_summary']
    assert summary['success'] is True
    assert [t['stage'] for t in summary['tests']] == ['baseline', 'candidate', 'final']
    assert '缺陷已复现' in summary['tests'][0]['meaning']
    assert summary['actual_model_requests'] == 0 and summary['model_attempts'] == 1
    assert summary['before']['content'] != summary['after']['content']
    assert 'original/code/leap_year.py' in summary['combined_diff']
    assert summary['checks'] and summary['skips']
    with TestClient(create_app(tmp_path)) as client:
        endpoint = client.get(f'/api/v3/tasks/{rid}/result-summary').json()
        report = client.get(f'/api/v3/tasks/{rid}/report').json()
        with zipfile.ZipFile(io.BytesIO(client.get(f'/api/v3/tasks/{rid}/bundle').content)) as archive:
            exported = json.loads(archive.read('report.json'))
        assert endpoint == summary == report['result_summary'] == exported['result_summary']


def test_legacy_missing_request_counts_are_not_reported_as_zero(tmp_path):
    service = TaskService(tmp_path)
    store = service.store
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    store.event(rid, {'event': 'agent_call', 'role': 'repairer'})
    store.finish_if_running(rid, 'failed', {'reason': 'legacy'})
    s = service.report(rid)['result_summary']
    assert s['actual_model_requests'] is None and s['tests'] == []
    assert s['before'] is None and s['after'] is None and s['success'] is False
