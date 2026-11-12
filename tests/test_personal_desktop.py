from dataclasses import replace
import json
from pathlib import Path
import shutil
import time

import httpx
import pytest

from app.live_model import Ledger, LiveModel, ModelBlocked
from app.personal import configure_runtime
from app.providers import validate_url
from app.role_config import RoleConfig
from app.model_profiles import ModelRouter
from app.remote_runner import RemoteRunner
from app.docker_runner import IsolationBlocked
from scripts.prepare_desktop import prepare_backend


def test_per_model_cny_price_snapshot_reaches_http_and_ledger(personal, tmp_path, monkeypatch):
    from app.personal_pricing import catalog
    root, values = personal
    p = next(p for p in catalog() if p['model'] == 'qwen-plus' and p['mode'] == 'non_thinking')
    values.update(base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', model=p['model'],
        model_prices={p['model']: p}, monetary_budget=True)
    monkeypatch.setenv('CODE_FIX_PERSONAL_BASE_URL', values['base_url'])
    monkeypatch.setenv('CODE_FIX_PERSONAL_BUDGET', 'money')
    monkeypatch.setenv('CODE_FIX_PERSONAL_DAILY_LIMIT', '5')
    configure_runtime(root, values)
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    router = ModelRouter(RoleConfig(root), Ledger(tmp_path / 'priced'),
        transports={'primary': httpx.MockTransport(handle)})
    record = router.call_for_role('repairer', [{'role': 'user', 'content': 'hello'}],
        run_id='priced', call_id='one', max_calls=12, deadline=time.monotonic()+10)
    assert captured[0]['enable_thinking'] is False
    assert record['cost']['amount_cny'] == '0.00012'
    assert router.binding('repairer')['personal_pricing'] == p
    assert router.ledger.summary('priced')['amount_cny'] == '0.00012'


@pytest.fixture
def personal(tmp_path, monkeypatch):
    root = tmp_path / 'app'
    prepare_backend(root)
    values = {'base_url': 'https://personal.example/v1', 'model': 'user-model',
              'max_output_tokens': 2048, 'token_parameter': 'max_tokens', 'price_version': 'personal-test'}
    for name, value in {
        'CODE_FIX_PERSONAL_MODE': '1', 'CODE_FIX_PERSONAL_BASE_URL': values['base_url'],
        'CODE_FIX_PERSONAL_KEY': 'test-no-real-key', 'CODE_FIX_PERSONAL_APPROVED': '1',
        'CODE_FIX_LIVE_APPROVED': '1', 'CODE_FIX_PERSONAL_BUDGET': 'usage',
    }.items():
        monkeypatch.setenv(name, value)
    configure_runtime(root, values)
    return root, values


def test_personal_bundle_is_empty_of_team_accounts_and_has_required_runtime_assets(tmp_path):
    root = tmp_path / 'bundle'
    prepare_backend(root)
    assert not (root / 'configs/model_profiles.json').exists()
    assert not (root / 'configs/agents.json').exists()
    assert 'sf_account' not in (root / 'configs/desktop-roles.json').read_text(encoding='utf-8')
    assert not (root / 'secrets').exists() and not (root / 'evidence').exists()
    for name in ('ui/app/index.html', 'prompts/repairer.md', 'configs/decision_rules.json', 'desktop-launch.py'):
        assert (root / name).is_file()


def test_personal_configuration_reaches_real_http_payload_without_team_keys(personal, tmp_path):
    root, values = personal
    config = RoleConfig(root)
    captured = []
    def handle(request):
        captured.append((str(request.url), request.headers['Authorization'], json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"status":"ok"}'}}],
                                        'usage': {'prompt_tokens': 5, 'completion_tokens': 8}})
    router = ModelRouter(config, Ledger(tmp_path / 'runs'),
                         transports={'primary': httpx.MockTransport(handle)})
    result = router.call_for_role('repairer', [{'role': 'user', 'content': 'hello'}],
                                 run_id='personal', call_id='p1', max_calls=2, deadline=time.monotonic()+10)
    assert result['status'] == 'ok'
    assert result['cost']['status'] == 'unpriced' and result['cost']['amount_usd'] is None
    assert captured == [(values['base_url']+'/chat/completions', 'Bearer test-no-real-key',
                         {'model': 'user-model', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 2048})]
    assert router.ledger.summary('personal')['amount_usd'] is None
    assert router.ledger.summary('personal')['status'] == 'unpriced'
    assert 'test-no-real-key' not in json.dumps(router.binding('repairer'))


def test_personal_role_output_limits_match_preview_wire_and_reservation(personal, tmp_path, monkeypatch):
    import sqlite3
    from app.configuration import ConfigurationStore
    from app.run_store import RunStore
    from app.personal_pricing import catalog, calculate
    root, values = personal
    price = next(p for p in catalog() if p['model'] == 'qwen-plus' and p['mode'] == 'non_thinking')
    values.update(base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', model='qwen-plus',
        max_output_tokens=8192, model_prices={'qwen-plus': price}, monetary_budget=True)
    monkeypatch.setenv('CODE_FIX_PERSONAL_BASE_URL', values['base_url'])
    monkeypatch.setenv('CODE_FIX_PERSONAL_BUDGET', 'money')
    monkeypatch.setenv('CODE_FIX_PERSONAL_DAILY_LIMIT', '5')
    configure_runtime(root, values)
    store = RunStore(tmp_path/'role-runs')
    configs = ConfigurationStore(store, root=root)
    limits = dict(repairer=8192, detector=4096, scheduler=2048, locator=2048)
    saved = configs.save({'strategy': 'dynamic', 'bindings': {r: 'personal_model' for r in limits},
        'parameters': {'max_rounds': 3, 'max_calls': 12, 'total_seconds': 300},
        'role_overrides': {r: {'generation': {'max_output_tokens': n}} for r, n in limits.items()}})
    captures = []
    def handle(request):
        captures.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    ledger = Ledger(store.root)
    router = ModelRouter(configs.runtime(saved['config_id']), ledger,
        transports={r: httpx.MockTransport(handle) for r in limits})
    for role, limit in limits.items():
        record = router.call_for_role(role, [{'role': 'user', 'content': 'hello'}],
            run_id='r', call_id=role, max_calls=12, deadline=time.monotonic()+10)
        assert record['status'] == 'ok'
        assert captures[-1]['max_tokens'] == saved['effective_roles'][role]['parameters']['max_tokens'] == limit
        with sqlite3.connect(ledger.path) as db:
            reserved = db.execute('SELECT reserved FROM personal_calls WHERE call_id=?', (role,)).fetchone()[0]
        assert reserved == calculate(price, {'prompt_tokens': 205, 'completion_tokens': limit}, reserve=True)[0]
    assert configs.get(saved['config_id']) == saved


def test_usage_budget_preserves_call_cap_and_unknown_blocks_mode_switch(personal, tmp_path):
    root, _ = personal
    settings = ModelRouter.resolve(RoleConfig(root))[0]['primary']
    ledger = Ledger(tmp_path / 'ledger')
    ledger.reserve('c1', 'r1', settings, 1, 100)
    ledger.settle('c1', {'prompt_tokens': 5, 'completion_tokens': 7}, settings)
    with pytest.raises(ModelBlocked, match='调用上限'):
        ledger.reserve('c2', 'r1', settings, 1, 100)
    ledger.reserve('c3', 'r2', settings, 2, 100)
    ledger.settle('c3', None, settings)
    with pytest.raises(ModelBlocked, match='未知'):
        ledger.reserve('c4', 'r3', settings, 2, 100)


@pytest.mark.parametrize('address', ['http://127.0.0.1:17880', 'http://localhost:17880', 'http://8.148.85.237:8780'])
def test_remote_http_requires_explicit_literal_loopback_tunnel(monkeypatch, address):
    monkeypatch.delenv('CODE_FIX_EXECUTION_SSH_TUNNEL', raising=False)
    with pytest.raises(IsolationBlocked):
        RemoteRunner(url=address, token='x'*40)
    monkeypatch.setenv('CODE_FIX_EXECUTION_SSH_TUNNEL', '1')
    if address == 'http://127.0.0.1:17880':
        runner = RemoteRunner(url=address, token='x'*40)
        runner.close()
    else:
        with pytest.raises(IsolationBlocked):
            RemoteRunner(url=address, token='x'*40)


def test_personal_provider_cannot_be_used_outside_explicit_owner_endpoint(personal, monkeypatch):
    assert validate_url('personal', 'https://personal.example/v1')
    assert not validate_url('personal', 'https://personal.example/other')
    assert not validate_url('personal', 'https://other.example/v1')
    monkeypatch.delenv('CODE_FIX_PERSONAL_MODE')
    assert not validate_url('personal', 'https://personal.example/v1')


@pytest.mark.parametrize('generation', [{'enable_thinking': False}, {'reasoning_effort': 'low'}])
def test_personal_thinking_parameter_reaches_http_and_snapshot(personal, tmp_path, monkeypatch, generation):
    from app.configuration import ConfigurationStore
    from app.run_store import RunStore
    from app.role_config import digest
    from app.role_overrides import capabilities
    from app.model_profiles import load_profiles
    root, values = personal
    values.update(base_url='https://maas.qianwenaiapi.com/compatible-mode/v1', model='deepseek-v4.1-flash-1017')
    monkeypatch.setenv('CODE_FIX_PERSONAL_BASE_URL', values['base_url'])
    configure_runtime(root, values)
    configs = ConfigurationStore(RunStore(tmp_path/'thinking'), root=root)
    draft = {'strategy': 'dynamic', 'bindings': {r: 'personal_model' for r in ('repairer','detector','scheduler','locator')},
        'parameters': {'max_rounds': 3, 'max_calls': 12, 'total_seconds': 300},
        'role_overrides': {'repairer': {'generation': generation}}}
    saved = configs.save(draft)
    captures = []
    def handle(request):
        captures.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    router = ModelRouter(configs.runtime(saved['config_id']), Ledger(tmp_path/'ledger'),
        transports={r: httpx.MockTransport(handle) for r in draft['bindings']})
    router.call_for_role('repairer', [], run_id='r', call_id='c', max_calls=12, deadline=time.monotonic()+10)
    for key, value in generation.items():
        assert captures[0][key] == saved['effective_roles']['repairer']['parameters'][key] == value
    # Old capability hashes are accepted only when no new thinking field is used.
    old = configs.save(draft | {'role_overrides': {}})
    old['capabilities_sha256'] = digest({ref: {k:v for k,v in capabilities(p).items()
        if k not in {'enable_thinking','reasoning_effort'}} for ref,p in load_profiles(root)['profiles'].items()})
    monkeypatch.setattr(configs, 'get', lambda identifier: old)
    assert configs.runtime(old['config_id']).role_overrides == {}
