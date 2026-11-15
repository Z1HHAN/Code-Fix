"""Configuration persistence/routing checks; no paid API or untrusted code execution."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.configuration import ConfigurationStore, ROOT
from app.errors import InputValidationError
from app.run_store import RunStore
from app.role_config import RoleConfig


def draft():
    return {'bindings': {'repairer': 'sf_account1_v32', 'detector': 'sf_account1_qwen36',
                         'scheduler': 'sf_account2_v32', 'locator': 'sf_account1_v32'},
            'parameters': {'max_rounds': 2, 'max_calls': 9, 'total_seconds': 120}, 'strategy': 'dynamic'}


def test_save_reload_deduplicates_and_task_parameters_are_frozen(tmp_path):
    store = RunStore(tmp_path)
    configs = ConfigurationStore(store)
    value = configs.save(draft())
    assert configs.save(draft()) == value
    assert ConfigurationStore(RunStore(tmp_path)).get(value['config_id']) == value
    runtime = configs.runtime(value['config_id'])
    assert runtime.snapshot['models']['detector']['profile_ref'] == draft()['bindings']['detector']
    assert runtime.require('scheduler')['model_ref'] == 'scheduler'
    task = store.create({'config_id': value['config_id'], **value['parameters']})
    with pytest.raises(InputValidationError, match='不一致'):
        store.create({'config_id': value['config_id'], 'max_rounds': 3})
    changed = draft(); changed['parameters']['max_calls'] = 10
    assert configs.save(changed)['config_id'] != value['config_id']
    assert store.get(task['run_id'])['task']['max_calls'] == 9


@pytest.mark.parametrize('change', [
    {'strategy': 'unknown'}, {'bindings': {'repairer': 'sf_account1_v32'}},
    {'parameters': {'max_rounds': 6, 'max_calls': 9, 'total_seconds': 120}},
    {'api_key': 'forbidden'}, {'parameters': {'max_rounds': True, 'max_calls': 9, 'total_seconds': 120}},
])
def test_invalid_drafts_rejected(tmp_path, change):
    with pytest.raises((InputValidationError, ValueError)):
        ConfigurationStore(RunStore(tmp_path)).save(draft() | change)


def test_no_unknown_profile_or_task_inline_runtime(tmp_path):
    configs = ConfigurationStore(RunStore(tmp_path))
    value = draft(); value['bindings']['detector'] = 'https://not-approved.example'
    with pytest.raises(InputValidationError):
        configs.save(value)
    with pytest.raises(InputValidationError):
        configs.store.create({'roles': {}})


def test_changed_registry_blocks_old_snapshot_before_call(tmp_path, monkeypatch):
    configs = ConfigurationStore(RunStore(tmp_path))
    saved = configs.save(draft())
    import app.configuration as module
    original = module.load_profiles
    monkeypatch.setattr(module, 'load_profiles', lambda root: original(root) | {'changed': True})
    with pytest.raises(ValueError, match='已变更'):
        configs.runtime(saved['config_id'])


def test_http_auth_and_save_create_read_without_model_calls(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post('/api/v3/configurations', json=draft()).status_code == 403
        headers = {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}
        catalog = client.get('/api/v3/configuration/catalog').json()
        names = {p['model'] for p in catalog['profiles'] if p['provider'] == 'siliconflow'}
        assert {'deepseek-ai/DeepSeek-V4.1-Flash', 'zai-org/GLM-5.3-Flash',
                'Pro/deepseek-ai/DeepSeek-V3.2', 'Qwen/Qwen3.6-27B', 'Pro/zai-org/GLM-5.1'} <= names
        assert all('api_key' not in p and 'base_url' not in p for p in catalog['profiles'])
        assert client.post('/api/v3/configurations/validate', headers=headers, json=draft()).status_code == 200
        response = client.post('/api/v3/configurations', headers=headers, json=draft())
        assert response.status_code == 201, response.text
        saved = response.json()
        created = client.post('/api/v3/tasks', headers=headers,
                              json={'config_id': saved['config_id'], **saved['parameters']}).json()
        assert created['status'] == 'created' and created['result'] is None
        assert client.get('/api/v3/tasks/'+created['run_id']+'/events').json() == []
        assert client.get('/api/v3/configurations/'+saved['config_id']).json() == saved
        report = client.get('/api/v3/tasks/'+created['run_id']+'/report').json()
        assert report['configuration_snapshot'] == saved


def test_corrupt_saved_payload_rejected(tmp_path):
    store = RunStore(tmp_path)
    configs = ConfigurationStore(store)
    saved = configs.save(draft())
    saved['parameters']['max_calls'] = 30
    with store.connect() as db:
        db.execute('UPDATE configurations SET payload=? WHERE config_id=?',
                   (json.dumps(saved), saved['config_id']))
    with pytest.raises(InputValidationError, match='校验失败'):
        configs.get(saved['config_id'])


def test_workflow_consumes_saved_bindings(tmp_path):
    # Real workflow, explicitly simulated model and executor: not repair acceptance.
    from test_workflow_v3 import ExecutorStub, ModelStub
    from app.workflow_v3 import Workflow
    store = RunStore(tmp_path)
    configs = ConfigurationStore(store)
    saved = configs.save(draft())
    rid = store.create({'mode': 'repair', 'config_id': saved['config_id'], **saved['parameters']})['run_id']
    store.claim(rid)
    status, result = Workflow(store, ExecutorStub(['assertion_failed', 'passed', 'passed']), ModelStub()).run(rid)
    assert status == 'success' and not result['model_api_called']
    evidence = json.loads((store.directory(rid)/'trace/configuration.json').read_text(encoding='utf-8'))
    assert evidence['roles'] == saved['roles']


def test_local_checks_report_each_blocker_without_exposing_credentials(monkeypatch):
    from app.configuration import profile_checks
    from app.model_profiles import load_profiles
    import app.local_credentials
    p = load_profiles(ROOT)['profiles']['sf_account1_v32'].copy()
    monkeypatch.setattr(app.local_credentials, 'read_secret', lambda name: 'private-test-credential')
    monkeypatch.delenv('CODE_FIX_LIVE_APPROVED', raising=False)
    monkeypatch.delenv(p['input_price_env'], raising=False)
    monkeypatch.delenv(p['output_price_env'], raising=False)
    p['billable_output_tokens'] = None
    checks = profile_checks(p)
    assert [c['ok'] for c in checks] == [False, True, False, False]
    assert 'private-test-credential' not in json.dumps(checks)
    monkeypatch.setenv('CODE_FIX_LIVE_APPROVED', '1')
    monkeypatch.setenv(p['approval_env'], '1')
    monkeypatch.setenv(p['input_price_env'], '2')
    monkeypatch.setenv(p['output_price_env'], '3')
    p['billable_output_tokens'] = 1000
    assert all(c['ok'] for c in profile_checks(p))


def test_credential_read_error_is_sanitized(monkeypatch):
    from app.configuration import profile_checks
    from app.model_profiles import load_profiles
    import app.local_credentials
    def unreadable(name):
        raise ValueError('sensitive-exception-content')
    monkeypatch.setattr(app.local_credentials, 'read_secret', unreadable)
    checks = profile_checks(load_profiles(ROOT)['profiles']['sf_account1_v32'])
    assert checks[1]['status'] == '无法读取'
    assert 'sensitive-exception-content' not in json.dumps(checks)

