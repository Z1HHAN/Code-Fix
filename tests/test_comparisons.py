"""Evidence-only paired summaries: offline executions never become formal data."""
from copy import deepcopy
import csv
import io
import json

from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.comparisons import ComparisonStore, export_csv
from app.errors import InputValidationError
from app.role_config import digest
from app.run_store import RunStore
from app.tasks_v3 import catalog
from test_workflow_v3 import execute


def pair(tmp_path):
    fixed = execute(tmp_path, ['assertion_failed', 'passed', 'passed'], {'strategy': 'fixed'})[-1]
    dynamic = execute(tmp_path, ['assertion_failed', 'passed', 'passed'], {'strategy': 'dynamic'})[-1]
    return {'title': 'offline contract', 'scope': 'offline', 'pairs': [
        {'project': 'leap', 'fixed': fixed, 'dynamic': dynamic, 'not_run_reason': {}},
        {'project': 'qb-gcd', 'fixed': None, 'dynamic': None,
         'not_run_reason': {'fixed': '未运行：资格待核对', 'dynamic': '未运行：资格待核对'}}]}


def test_explicit_manifest_rates_evidence_and_durable_snapshot(tmp_path):
    value = pair(tmp_path)
    store = RunStore(tmp_path)
    before = store.list()
    comparisons = ComparisonStore(store)
    result = comparisons.create(value)
    for strategy in ('fixed', 'dynamic'):
        summary = result['summary'][strategy]
        assert summary['planned'] == 2 and summary['executed'] == summary['success'] == 1
        assert summary['not_run'] == 1
        assert summary['success_rate_all'] == .5 and summary['success_rate_executed'] == 1
        row = result['pairs'][0][strategy]
        assert row['candidate_rounds'] == 1 and row['requests'] == 0
        assert row['cost']['amount_cny'] is None and row['cost']['status'] == 'not_applicable'
        assert summary['cost']['cny']['total'] is None
        assert row['report_url'].endswith('/report') and row['elapsed_seconds'] is not None
    assert comparisons.create(value) == result
    assert ComparisonStore(RunStore(tmp_path)).get(result['comparison_id']) == result
    assert store.list() == before
    assert len(comparisons.list()) == 1
    csv = export_csv(result)
    assert csv.startswith('\ufeffproject,') and value['pairs'][0]['fixed'] in csv and '未运行' in csv


def test_different_configuration_ids_allow_same_effective_conditions(tmp_path):
    from app.configuration import ConfigurationStore
    from test_configuration import draft
    store = RunStore(tmp_path)
    configs = ConfigurationStore(store)
    ids, runs = {}, {}
    for strategy in ('fixed', 'dynamic'):
        saved = configs.save(draft() | {'strategy': strategy, 'role_overrides': {}})
        ids[strategy] = saved['config_id']
        runs[strategy] = execute(tmp_path, ['assertion_failed', 'passed', 'passed'],
                                  {'config_id': saved['config_id'], **saved['parameters'], 'strategy': strategy})[-1]
    assert ids['fixed'] != ids['dynamic']
    result = ComparisonStore(store).create({'title': 'different IDs, same conditions', 'scope': 'offline',
        'pairs': [{'project': 'leap', **runs, 'not_run_reason': {}}]})
    assert result['pairs'][0]['paired']


@pytest.mark.parametrize('difference', ['task_limits', 'bindings', 'prompts_sha256', 'code', 'image'])
def test_any_frozen_condition_difference_rejects_pair(tmp_path, difference):
    value = pair(tmp_path)
    store = RunStore(tmp_path)
    frozen_path = store.directory(value['pairs'][0]['dynamic']) / 'trace/experiment.json'
    frozen = json.loads(frozen_path.read_text(encoding='utf-8'))
    frozen['identity'][difference] = {'changed': True}
    frozen['identity_sha256'] = digest(frozen['identity'])
    frozen_path.write_text(json.dumps(frozen), encoding='utf-8')
    with pytest.raises(InputValidationError, match='冻结条件不一致'):
        ComparisonStore(store).create(value)


def test_stubs_cannot_claim_formal_or_controlled_real_verification(tmp_path):
    value = pair(tmp_path)
    store = RunStore(tmp_path)
    value['scope'] = 'controlled'
    with pytest.raises(InputValidationError, match='真实执行器'):
        ComparisonStore(store).create(value)
    value['scope'] = 'formal'
    with pytest.raises(InputValidationError, match='已批准'):
        ComparisonStore(store).create(value)


def test_missing_freeze_and_current_config_are_never_substituted(tmp_path):
    value = pair(tmp_path)
    store = RunStore(tmp_path)
    (store.directory(value['pairs'][0]['dynamic']) / 'trace/experiment.json').unlink()
    with pytest.raises(InputValidationError, match='缺少冻结'):
        ComparisonStore(store).create(value)


def test_unknown_cost_and_blocked_are_not_success_or_zero(tmp_path):
    value = pair(tmp_path)
    store = RunStore(tmp_path)
    rid = value['pairs'][0]['fixed']
    run = store.get(rid)
    result = run['result'] | {'cost': {'status': 'unknown', 'amount_cny': '0'}, 'reason': '=unsafe formula'}
    with store.connect() as db:
        db.execute('UPDATE runs SET status=?,result=? WHERE run_id=?', ('blocked', json.dumps(result), rid))
    saved = ComparisonStore(store).create(value)
    assert saved['summary']['fixed']['blocked'] == 1
    assert saved['summary']['fixed']['failed'] == 0
    assert saved['summary']['fixed']['success_rate_all'] == 0
    assert saved['pairs'][0]['fixed']['cost']['amount_cny'] is None
    assert "'=unsafe formula" in export_csv(saved)


def test_pending_and_duplicate_runs_rejected_and_missing_reasons_required(tmp_path):
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair', 'strategy': 'fixed'})['run_id']
    value = {'title': 'pending', 'scope': 'offline', 'pairs': [
        {'project': 'leap', 'fixed': rid, 'dynamic': None, 'not_run_reason': {'dynamic': 'pending'}}]}
    with pytest.raises(InputValidationError, match='已结束'):
        ComparisonStore(store).create(value)
    value['pairs'][0]['fixed'] = None
    with pytest.raises(InputValidationError, match='说明原因'):
        ComparisonStore(store).create(value)


def test_http_create_read_and_exports_never_start_tasks(tmp_path):
    value = pair(tmp_path)
    store = RunStore(tmp_path)
    before = [store.snapshot(r['run_id']) for r in store.list()]
    with TestClient(create_app(tmp_path)) as client:
        assert client.post('/api/v3/comparisons', json=value).status_code == 403
        headers = {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}
        response = client.post('/api/v3/comparisons', headers=headers, json=value)
        assert response.status_code == 201, response.text
        saved = response.json()
        url = '/api/v3/comparisons/' + saved['comparison_id']
        assert client.get(url).json() == saved
        assert client.get(url + '/export').json() == saved
        assert client.get(url + '/export?format=csv').status_code == 200
        assert len(client.get('/api/v3/comparisons').json()) == 1
    assert [store.snapshot(r['run_id']) for r in store.list()] == before


def test_approved_plan_can_record_missing_qualification_without_fake_run(tmp_path):
    value = {'title': 'await B', 'scope': 'formal', 'pairs': [
        {'project': p, 'fixed': None, 'dynamic': None,
         'not_run_reason': {'fixed': '待真实运行', 'dynamic': '待真实运行'}}
        for p in ('qb-get_factors', 'qb-gcd', 'qb-max_sublist_sum')]}
    result = ComparisonStore(RunStore(tmp_path)).create(value)
    assert result['summary']['fixed']['success_rate_all'] == 0
    assert result['summary']['fixed']['success_rate_executed'] is None
    assert result['summary']['fixed']['not_run'] == 3


def test_full_quixbugs_manifest_exports_without_tasks_or_changing_old_snapshot(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    service = app.state.service
    monkeypatch.setattr(service, 'run_claimed', lambda *_: pytest.fail('Comparison dispatched a task'))
    projects = sorted(p for p in catalog() if p.startswith('qb-'))
    assert len(projects) == 40
    value = {'title': '全量40题，资格和真实配对待核', 'scope': 'formal', 'pairs': [
        {'project': p, 'fixed': None, 'dynamic': None,
         'not_run_reason': {'fixed': '待' * 500, 'dynamic': '待' * 500}}
        for p in projects]}
    body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    assert 8192 < len(body) < 131072
    with TestClient(app) as client:
        headers = {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}
        old = client.post('/api/v3/comparisons', headers=headers,
                          json=value | {'title': '历史三题快照', 'pairs': value['pairs'][:3]})
        assert old.status_code == 201, old.text
        old_url = '/api/v3/comparisons/' + old.json()['comparison_id']
        old_bytes = client.get(old_url).content
        response = client.post('/api/v3/comparisons', headers=headers, content=body)
        assert response.status_code == 201, response.text
        saved = response.json()
        url = '/api/v3/comparisons/' + saved['comparison_id']
        for strategy in ('fixed', 'dynamic'):
            summary = saved['summary'][strategy]
            assert summary['planned'] == summary['not_run'] == 40
            assert summary['ran'] == summary['executed'] == summary['success'] == summary['blocked'] == 0
            assert summary['success_rate_all'] == 0
            assert summary['success_rate_executed'] is None
            assert summary['cost']['cny']['total'] is None
        assert client.get(url).json() == client.get(url + '/export').json() == saved
        exported = client.get(url + '/export?format=csv')
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
        assert len(rows) == 80
        assert {r['project'] for r in rows} == set(projects)
        assert all(r['status'] == 'not_run' and not r['run_id'] and not r['amount_cny'] for r in rows)
        # Chunked writes obey the same bound, while task creation retains 8 KiB.
        streamed = client.post('/api/v3/comparisons', headers=headers,
                               content=iter(body[i:i+16000] for i in range(0, len(body), 16000)))
        assert streamed.status_code == 201, streamed.text
        assert streamed.json() == saved
        assert client.post('/api/v3/tasks', headers=headers, content=body).status_code == 413
        assert client.post('/api/v3/comparisons', headers=headers, content=b' ' * 131073).status_code == 413
        assert client.post('/api/v3/comparisons', headers=headers,
                           content=iter([b' ' * 70000, b' ' * 70000])).status_code == 413
        assert client.get(old_url).content == old_bytes
        assert len(client.get('/api/v3/comparisons').json()) == 2
        assert client.get('/api/v3/tasks').json() == []
    assert service.store.list() == []
