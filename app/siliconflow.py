"""Provider-scoped metadata checks and capped JSON smoke, never code execution."""
import asyncio
import json
import os
from pathlib import Path
import time
import uuid
from decimal import Decimal

import httpx

from .account_budget import AccountBudget
from .config import read_json
from .live_model import Ledger, LiveModel, ModelBlocked
from .local_credentials import read_secret
from .model_profiles import load_profiles, profile_settings
from .providers import validate_url

ROOT = Path(__file__).resolve().parents[1]


def budget_status(run_root, root=ROOT):
    registry = load_profiles(root)
    ledger = Ledger(run_root)
    if os.environ.get('CODE_FIX_PERSONAL_MODE') == '1':
        import sqlite3
        with sqlite3.connect(ledger.path) as db:
            blocked = bool(db.execute("SELECT 1 FROM calls WHERE status IN ('pending','unknown')").fetchone()
                           or db.execute("SELECT 1 FROM cny_calls WHERE status IN ('pending','unknown')").fetchone()
                           or db.execute("SELECT 1 FROM personal_calls WHERE status IN ('pending','unknown')").fetchone())
            held, reserved = db.execute("SELECT count(*),coalesce(sum(reserved),0) FROM personal_calls WHERE status='reserved_unknown'").fetchone()
        return {'personal': True, 'accounts': [], 'paid_calls_blocked': blocked,
                'held_unknown_calls': held, 'held_upper_cny': str(Decimal(reserved)/1000000),
                'monetary_budget_enabled': os.environ.get('CODE_FIX_PERSONAL_BUDGET') != 'usage',
                'platform_balance_verified': False,
                'note': '个人模型配置；费用待核对按预留上限占用累计预算，不能跨授权绕过；未定价或历史未知仍阻断。逐次用量和估算见运行记录，不是服务商实时余额。'}
    result = AccountBudget(ledger.path).snapshot()
    actual = {a['account_id']: a for a in result['accounts']}
    for aid, account in registry.get('accounts', {}).items():
        if aid not in actual:
            result['accounts'].append({'account_id': aid, 'provider_id': account['provider_id'],
                'currency': 'CNY', 'status': 'declared_not_started', 'authorized_cny': account['total_limit_cny'],
                'estimated_spent_cny': '0', 'reserved_cny': '0', 'unsettled_calls': 0,
                'estimated_remaining_cny': account['total_limit_cny'],
                'warning_threshold_cny': account['warning_balance_cny'], 'low_balance': False})
    result['platform_balance_verified'] = False
    result['note'] = '仅本项目账本估算，包含预留；不含平台其他消费，真实余额请核对控制台'
    return result


# 元数据端点按服务商登记，调用点不再写死任何厂商域名。
# 未登记的服务商不会被查询，也不会被拼接出请求。
PROVIDER_METADATA = {
    'siliconflow': {
        'base_url': 'https://api.siliconflow.cn/v1',
        'catalog_path': 'configs/siliconflow_catalog.json',
        'params': {'type': 'text', 'sub_type': 'chat'},
    },
}


def metadata_endpoint(provider):
    """Resolve a provider metadata endpoint; unknown or malformed entries are refused."""
    endpoint = PROVIDER_METADATA.get(provider)
    if not isinstance(endpoint, dict):
        raise ValueError('未登记的服务商元数据端点')
    # 端点须为 https、无凭证/查询、且主机在该服务商白名单内；params 必须是对象。
    if not isinstance(endpoint.get('params'), dict) or not endpoint.get('catalog_path'):
        raise ValueError('服务商元数据端点配置无效')
    if not validate_url(provider, endpoint.get('base_url')):
        raise ValueError('服务商元数据端点配置无效')
    return endpoint


async def _models(key, endpoint, transport=None):
    async with asyncio.timeout(15):
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=15) as client:
            async with client.stream('GET', endpoint['base_url'].rstrip('/') + '/models',
                    params=endpoint.get('params') or {}, headers={'Authorization': 'Bearer ' + key}) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1_048_576:
                        raise ValueError('模型列表响应过大')
                data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise ValueError('模型列表格式无效')
    return {row['id'] for row in data['data'] if isinstance(row, dict) and isinstance(row.get('id'), str)}


def check_models(root=ROOT, *, transport=None, provider='siliconflow'):
    endpoint = metadata_endpoint(provider)
    registry = load_profiles(root)
    catalog = read_json(root / endpoint['catalog_path'])
    allowed = [m['model'] for m in catalog['models']]
    scope = 'offline_transport' if transport is not None else 'live_metadata_only'
    accounts = []
    for aid, account in registry.get('accounts', {}).items():
        record = {'account_id': aid, 'provider_id': account['provider_id'], 'model_api_called': False,
                  'metadata_request_made': False, 'paid_chat_requests': 0, 'scope': scope}
        # 跨服务商凭证隔离：非本次查询服务商的账号不读密钥、不发请求。
        if account['provider_id'] != provider:
            record.update(status='skipped', reason='该账号不属于本次查询的服务商，未读取密钥、未请求平台')
            accounts.append(record)
            continue
        try:
            key = read_secret(account['api_key_env'])
        except (ValueError, OSError):
            record.update(status='blocked', reason='本机加密凭证不可读取，未请求平台')
            accounts.append(record)
            continue
        if not key:
            record.update(status='blocked', reason='本机密钥未配置，未请求平台')
        else:
            try:
                record['metadata_request_made'] = True
                models = asyncio.run(_models(key, endpoint, transport))
                record.update(status='ok', available=[m for m in allowed if m in models],
                              unavailable=[m for m in allowed if m not in models])
            except Exception as exc:
                # Do not echo provider errors, request headers, credentials or account identity.
                record.update(status='failed', error_type=type(exc).__name__,
                              http_status=getattr(getattr(exc, 'response', None), 'status_code', None))
        accounts.append(record)
    queried = {a['provider_id'] for a in accounts if a['metadata_request_made']}
    return {'provider': provider, 'provider_count': len(queried), 'accounts': accounts,
            'queried_account_count': sum(1 for a in accounts if a['metadata_request_made']),
            'paid_chat_requests': 0, 'platform_balance_verified': False}


def smoke(run_root, profile_ref='sf_account1_v32', root=ROOT, *, transport=None):
    registry = load_profiles(root)
    profile = registry['profiles'].get(profile_ref)
    if not profile or profile['provider_id'] != 'siliconflow':
        raise ModelBlocked('请选择已登记硅基流动档案')
    settings = profile_settings(profile, registry)
    from dataclasses import replace
    settings = replace(settings, max_output_tokens=64)
    ledger = Ledger(run_root)
    rid = 'smoke-' + uuid.uuid4().hex
    folder = Path(run_root) / rid
    folder.mkdir(parents=True)
    record = LiveModel(settings, ledger, transport=transport).call(
        [{'role': 'system', 'content': 'Return only a JSON object. No code or explanations.'},
         {'role': 'user', 'content': 'Return {"ok":true}.'}],
        run_id=rid, call_id='m-' + uuid.uuid4().hex, max_calls=1, deadline=time.monotonic()+settings.timeout)
    # Retain transport record only in local runtime; no raw credential or reasoning.
    valid = False
    try:
        valid = record['status'] == 'ok' and json.loads(record.get('raw_text', '')) == {'ok': True}
    except (ValueError, TypeError):
        pass
    result = {k: v for k, v in record.items() if k != 'raw_text'}
    result.update(run_id=rid, scope='JSON API smoke only; no Agents, candidate or tests executed',
                  json_contract_passed=valid, budget=budget_status(run_root, root),
                  pricing={'currency': 'CNY', 'price_version': settings.price_version,
                           'input_per_million': str(settings.input_per_million),
                           'output_per_million': str(settings.output_per_million),
                           'billable_output_tokens': settings.billable_output_tokens},
                  effective_parameters={settings.token_limit_parameter: settings.max_output_tokens,
                                        **(settings.request_parameters or {})})
    (folder / 'smoke.json').write_bytes((json.dumps(result, ensure_ascii=False, indent=2)+'\n').encode())
    return result
