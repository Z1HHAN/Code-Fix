"""Offline account-budget acceptance; no real keys, network or public execution."""
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import siliconflow as siliconflow_module
from app.account_budget import AccountBudget
from app.api import create_app
from app.config import ConfigError
from app.live_model import Ledger, LiveModel, ModelBlocked
from app.local_credentials import read_secret, save_local
from app.model_profiles import load_profiles, profile_settings
from app.siliconflow import budget_status, check_models, smoke
from test_agents_v3 import settings

ROOT = Path(__file__).resolve().parents[1]


def cny():
    return settings(currency='CNY', account_id='account1', account_limit=Decimal(16),
                    warning_balance=Decimal(3), team_limit=Decimal(32),
                    input_per_million=Decimal(1), output_per_million=Decimal(1),
                    max_output_tokens=10, billable_output_tokens=20)


def charge(ledger, key, setting, yuan=1, run='run'):
    ledger.reserve(key, run, setting, 100, yuan * 1_000_000)
    return ledger.settle(key, {'prompt_tokens': yuan * 1_000_000, 'completion_tokens': 0}, setting)


def registry_copy(tmp_path):
    (tmp_path / 'configs').mkdir()
    for name in ('model_profiles.json', 'siliconflow_catalog.json'):
        shutil.copyfile(ROOT / 'configs' / name, tmp_path / 'configs' / name)
    return load_profiles(tmp_path)


def enable(monkeypatch):
    monkeypatch.setenv('CODE_FIX_LIVE_APPROVED', '1')
    monkeypatch.setenv('CODE_FIX_SF_APPROVED', '1')
    monkeypatch.setenv('CODE_FIX_SF_REQUEST_SECONDS', '2')
    monkeypatch.setenv('CODE_FIX_SF_ACCOUNT1_KEY', 'sk-offline-account1')
    monkeypatch.setenv('CODE_FIX_SF_ACCOUNT2_KEY', 'sk-offline-account2')
    monkeypatch.setenv('CODE_FIX_SF_V32_INPUT_CNY', '4')
    monkeypatch.setenv('CODE_FIX_SF_V32_OUTPUT_CNY', '6')
    monkeypatch.setenv('CODE_FIX_SF_V41_FLASH_INPUT_CNY', '2')
    monkeypatch.setenv('CODE_FIX_SF_V41_FLASH_OUTPUT_CNY', '8')
    monkeypatch.setenv('CODE_FIX_ALIYUN_APPROVED', '1')
    monkeypatch.setenv('CODE_FIX_ALIYUN_V41_INPUT_CNY', '2')
    monkeypatch.setenv('CODE_FIX_ALIYUN_V41_OUTPUT_CNY', '8')
    monkeypatch.setenv('CODE_FIX_ALIYUN_REQUEST_SECONDS', '2')


def test_native_cny_cumulative_across_dates_models_and_restart(tmp_path):
    ledger, s = Ledger(tmp_path), cny()
    charge(ledger, 'yesterday', s, 10)
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE cny_calls SET created_at='2000-01-01'")
    charge(Ledger(tmp_path), 'today', replace(s, model='another-approved-model'), 3)
    snapshot = AccountBudget(ledger.path).snapshot()
    assert snapshot['accounts'][0]['estimated_remaining_cny'] == '3'
    assert snapshot['accounts'][0]['low_balance']
    assert snapshot['alerts'][0]['kind'] == 'low_balance'
    assert snapshot['alerts'][0]['basis'] == 'project_ledger_estimate_not_platform_balance'
    with pytest.raises(ModelBlocked, match='累计人民币额度不足'):
        charge(ledger, 'over-cap', s, 4)
    second = replace(s, account_id='account2', api_key='another-offline-key')
    charge(ledger, 'second', second, 2)
    accounts = AccountBudget(ledger.path).snapshot()['accounts']
    assert [a['estimated_remaining_cny'] for a in accounts] == ['3', '14']
    assert len({a['provider_id'] for a in accounts}) == 1
    assert ledger.summary('run')['amount_cny'] == '15'
    assert ledger.summary('run')['amount_usd'] is None


def test_no_low_balance_above_three_and_alert_is_durable(tmp_path):
    ledger, s = Ledger(tmp_path), cny()
    charge(ledger, 'first', s, 12)
    assert not AccountBudget(ledger.path).snapshot()['alerts']
    charge(ledger, 'second', s, 1)
    charge(ledger, 'third', s, 1)
    state = AccountBudget(Ledger(tmp_path).path).snapshot()
    assert state['accounts'][0]['estimated_remaining_cny'] == '2'
    assert len([x for x in state['alerts'] if x['kind'] == 'low_balance']) == 1


@pytest.mark.parametrize('change', [{'account_limit': Decimal(17)}, {'team_limit': Decimal(33)},
                                  {'account_id': 'renamed-alias'}])
def test_configuration_cannot_reset_used_credit(tmp_path, change):
    ledger, s = Ledger(tmp_path), cny()
    charge(ledger, 'first', s)
    with pytest.raises(ModelBlocked):
        charge(Ledger(tmp_path), 'second', replace(s, **change))


def test_global_unknown_blocks_all_accounts_currencies_even_previous_day(tmp_path):
    ledger, s = Ledger(tmp_path), cny()
    ledger.reserve('uncertain', 'r', s, 3, 100)
    result = ledger.settle('uncertain', None, s)
    assert result['amount_cny'] is None and result['budget']['paid_calls_blocked']
    assert result['budget']['accounts'][0]['estimated_remaining_cny'] is None
    for other in [s, replace(s, account_id='account2', api_key='other-key'), settings()]:
        with pytest.raises(ModelBlocked, match='费用未知'):
            Ledger(tmp_path).reserve('next', 'different-run', other, 3, 100)
    assert AccountBudget(ledger.path).snapshot()['alerts'][0]['kind'] == 'cost_unknown'
    with pytest.raises(ModelBlocked, match='已经结算'):
        ledger.settle('uncertain', {'prompt_tokens': 1, 'completion_tokens': 0}, s)


def test_usd_uncertainty_and_call_count_apply_to_cny(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.reserve('old', 'r', settings(), 1, 100)
    with pytest.raises(ModelBlocked, match='费用未知'):
        ledger.reserve('new', 'other', cny(), 1, 100)
    ledger.settle('old', {'prompt_tokens': 5, 'completion_tokens': 1}, settings())
    with pytest.raises(ModelBlocked, match='调用上限'):
        ledger.reserve('new', 'r', cny(), 1, 100)
    charge(ledger, 'new', cny(), run='r')
    result = ledger.summary('r')
    assert result['status'] == 'mixed_currencies'
    assert result['amount_usd'] == '0.000007' and result['amount_cny'] == '1'


def test_cny_http_parameters_reasoning_reservation_and_no_double_settlement(tmp_path):
    s = replace(cny(), token_limit_parameter='max_tokens', request_parameters={'enable_thinking': False})
    ledger, seen = Ledger(tmp_path), []
    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body['max_tokens'] == 10 and body['enable_thinking'] is False
        return httpx.Response(200, json={'usage': {'prompt_tokens': 8, 'completion_tokens': 19},
                                        'choices': [{'message': {'content': ''}}]})
    record = LiveModel(s, ledger, transport=httpx.MockTransport(respond)).call(
        [{'role': 'user', 'content': 'test'}], run_id='r', call_id='c', max_calls=1, deadline=time.monotonic()+5)
    assert record['status'] != 'ok' and record['cost']['amount_cny'] == '0.000027'
    assert ledger.summary('r')['status'] == 'estimated'
    assert record['model_api_called'] is False and len(seen) == 1
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT reserved_microcny FROM cny_calls').fetchone()[0] == 224


@pytest.mark.parametrize('response', [httpx.Response(429, json={'error': 'sk-offline-not-exported'}),
    httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]}),
    httpx.Response(200, json={'choices': [], 'usage': {'prompt_tokens': 10000, 'completion_tokens': 1}})])
def test_unknown_http_charge_never_becomes_zero(tmp_path, response):
    record = LiveModel(cny(), Ledger(tmp_path), transport=httpx.MockTransport(lambda _: response)).call(
        [{'role': 'user', 'content': 'test'}], run_id='r', call_id='c', max_calls=1, deadline=time.monotonic()+5)
    assert record['cost']['status'] == 'unknown' and record['cost']['amount_cny'] is None
    assert record['cost']['budget']['paid_calls_blocked']
    assert 'sk-offline-not-exported' not in json.dumps(record)


def test_profiles_cover_two_providers_and_six_models(tmp_path, monkeypatch):
    registry = registry_copy(tmp_path)
    enable(monkeypatch)
    assert len(registry['profiles']) == 11 and len(registry['accounts']) == 3
    assert len({p['model'] for p in registry['profiles'].values()}) == 6
    assert {p['provider_id'] for p in registry['profiles'].values()} == {'siliconflow', 'aliyun_maas'}
    resolved = profile_settings(registry['profiles']['sf_account1_v32'], registry)
    assert resolved.account_limit == 16 and resolved.warning_balance == 3 and resolved.team_limit == 32
    assert resolved.currency == 'CNY' and resolved.billable_output_tokens == 163840
    flash = profile_settings(registry['profiles']['sf_account1_v41_flash'], registry)
    assert flash.model == 'deepseek-ai/DeepSeek-V4.1-Flash' and flash.billable_output_tokens == 8192
    monkeypatch.setenv('CODE_FIX_ALIYUN_MAAS_KEY', 'sk-offline-aliyun')
    aliyun = profile_settings(registry['profiles']['aliyun_maas_ds_v41_flash'], registry)
    assert aliyun.model == 'deepseek-v4.1-flash-1017' and aliyun.billable_output_tokens == 8192
    assert aliyun.base_url.endswith('/compatible-mode/v1') and aliyun.account_id == 'aliyun_maas'
    with pytest.raises(ModelBlocked, match='计费输出上限未核实'):
        profile_settings(registry['profiles']['sf_account1_glm53_flash'], registry)
    monkeypatch.delenv('CODE_FIX_SF_V32_INPUT_CNY')
    with pytest.raises(ModelBlocked):
        profile_settings(registry['profiles']['sf_account1_v32'], registry)


@pytest.mark.parametrize('field,value', [('warning_balance_cny', '16'), ('total_limit_cny', 'NaN'),
                                        ('api_key_env', None)])
def test_invalid_quota_registry_has_actionable_config_error(tmp_path, field, value):
    registry = registry_copy(tmp_path)
    registry['accounts']['sf_account1'][field] = value
    (tmp_path / 'configs/model_profiles.json').write_text(json.dumps(registry), encoding='utf-8')
    with pytest.raises(ConfigError):
        load_profiles(tmp_path)


def test_metadata_mock_and_http_failure_never_export_keys(tmp_path, monkeypatch):
    registry_copy(tmp_path)
    enable(monkeypatch)
    allowed = json.loads((ROOT/'configs/siliconflow_catalog.json').read_text(encoding='utf-8'))['models']
    seen = []
    def respond(request):
        seen.append(request)
        assert request.method == 'GET' and request.url.host == 'api.siliconflow.cn'
        assert request.url.path == '/v1/models'
        if len(seen) == 2:
            return httpx.Response(403, json={'error': 'sk-offline-account2'})
        return httpx.Response(200, json={'data': [{'id': m['model']} for m in allowed]})
    result = check_models(tmp_path, transport=httpx.MockTransport(respond))
    assert result['provider_count'] == 1 and result['paid_chat_requests'] == 0
    assert len(result['accounts'][0]['available']) == 5
    assert result['accounts'][1]['http_status'] == 403
    assert all(a['scope'] == 'offline_transport' for a in result['accounts'])
    assert 'sk-offline-' not in json.dumps(result) and len(seen) == 2


def test_metadata_missing_key_does_not_request(tmp_path, monkeypatch):
    registry_copy(tmp_path)
    monkeypatch.setattr('app.siliconflow.read_secret', lambda _: None)
    def forbidden(_):
        pytest.fail('Missing keys must not trigger any HTTP request')
    result = check_models(tmp_path, transport=httpx.MockTransport(forbidden))
    assert all(not a['metadata_request_made'] for a in result['accounts'])
    assert [a['status'] for a in result['accounts'] if a['provider_id'] == 'siliconflow'] == ['blocked', 'blocked']
    assert all(a['status'] == 'skipped' for a in result['accounts'] if a['provider_id'] != 'siliconflow')


def test_provider_check_never_sends_third_party_key(tmp_path, monkeypatch):
    """跨服务商凭证隔离：第三方账号的key绝不出现在任何发出请求里。"""
    registry_copy(tmp_path)
    enable(monkeypatch)
    monkeypatch.setenv('CODE_FIX_ALIYUN_MAAS_KEY', 'sk-offline-third-party-secret')
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'data': []})
    result = check_models(tmp_path, transport=httpx.MockTransport(respond))
    sent = [request.headers.get('authorization', '') for request in seen]
    assert len(seen) == 2
    assert {request.url.host for request in seen} == {'api.siliconflow.cn'}
    assert not any('third-party-secret' in value for value in sent)
    abandoned = [a for a in result['accounts'] if a['provider_id'] != 'siliconflow']
    assert abandoned and all(a['status'] == 'skipped' and not a['metadata_request_made'] for a in abandoned)
    assert 'sk-offline-third-party-secret' not in json.dumps(result)


def test_metadata_endpoint_is_resolved_per_provider_not_hardcoded(tmp_path, monkeypatch):
    registry_copy(tmp_path)
    enable(monkeypatch)
    monkeypatch.setitem(siliconflow_module.PROVIDER_METADATA, 'siliconflow',
                        {**siliconflow_module.PROVIDER_METADATA['siliconflow'],
                         'base_url': 'https://metadata.invalid/v9'})
    hosts = []
    def respond(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json={'data': []})
    check_models(tmp_path, transport=httpx.MockTransport(respond))
    assert hosts == ['metadata.invalid', 'metadata.invalid']
    with pytest.raises(ValueError, match='未登记的服务商元数据端点'):
        check_models(tmp_path, transport=httpx.MockTransport(respond), provider='unregistered')


def test_metadata_endpoint_rejects_non_https_or_missing_catalog(tmp_path, monkeypatch):
    registry_copy(tmp_path)
    enable(monkeypatch)
    for broken in ({'base_url': 'http://plain.example/v1', 'catalog_path': 'configs/siliconflow_catalog.json', 'params': {}},
                   {'base_url': 'https://ok.example/v1', 'params': {}}):
        monkeypatch.setitem(siliconflow_module.PROVIDER_METADATA, 'siliconflow', broken)
        with pytest.raises(ValueError, match='服务商元数据端点配置无效'):
            check_models(tmp_path, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'data': []})))


def test_smoke_refuses_non_siliconflow_profile_before_any_request(tmp_path, monkeypatch):
    """provider-smoke的白名单必须在读取密钥与发出请求之前拦住阿里云档案。"""
    registry_copy(tmp_path)
    enable(monkeypatch)
    monkeypatch.setenv('CODE_FIX_ALIYUN_MAAS_KEY', 'sk-offline-third-party-secret')
    def forbidden(_):
        pytest.fail('非硅基流动档案不得触发任何HTTP请求')
    with pytest.raises(ModelBlocked, match='硅基流动'):
        smoke(tmp_path, 'aliyun_maas_ds_v41_flash', transport=httpx.MockTransport(forbidden))


def test_local_api_estimated_balance_and_offline_smoke_are_separate(tmp_path, monkeypatch):
    enable(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        value = client.get('/api/v3/budget').json()
        assert not value['platform_balance_verified']
        assert [a['estimated_remaining_cny'] for a in value['accounts']] == ['16', '16', '16']
        assert all(a['status'] == 'declared_not_started' for a in value['accounts'])
    response = {'id': 'offline-only', 'model': 'offline-test', 'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
                'choices': [{'message': {'content': '{"ok":true}'}}]}
    record = smoke(tmp_path, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    assert record['json_contract_passed'] and not record['model_api_called']
    assert record['mode'] == 'test_transport' and 'no Agents' in record['scope']
    assert record['cost']['amount_cny'] == '0.00007'
    assert record['effective_parameters']['max_tokens'] == 64
    assert 'sk-offline-' not in json.dumps(record)
    assert budget_status(tmp_path)['accounts'][0]['estimated_spent_cny'] == '0.00007'


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI is machine/user specific')
def test_dpapi_roundtrip_refuses_overwrite_and_env_has_precedence(tmp_path, monkeypatch):
    path = tmp_path / 'credentials.json'
    fake = {'CODE_FIX_SF_ACCOUNT1_KEY': 'sk-offline-account1', 'CODE_FIX_SF_ACCOUNT2_KEY': 'sk-offline-account2'}
    for name in fake:
        monkeypatch.delenv(name, raising=False)
    save_local(fake, path)
    assert all(value not in path.read_text() for value in fake.values())
    assert read_secret('CODE_FIX_SF_ACCOUNT1_KEY', path) == 'sk-offline-account1'
    with pytest.raises(FileExistsError):
        save_local(fake, path)
    monkeypatch.setenv('CODE_FIX_SF_ACCOUNT1_KEY', 'environment-only-test')
    assert read_secret('CODE_FIX_SF_ACCOUNT1_KEY', path) == 'environment-only-test'
