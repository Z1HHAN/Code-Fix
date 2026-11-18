"""Desktop connection checks: free metadata/probe, or one explicit paid test.

Credentials arrive on stdin from the desktop main process, never command lines.
Checks do not create repair tasks or run candidate code.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

import httpx

from .docker_runner import IsolationBlocked
from .live_model import Ledger, LiveModel, ModelBlocked
from .remote_execution import PROTOCOL
from .remote_runner import RemoteRunner


def item(identifier, title, status, detail, action=''):
    return dict(id=identifier, title=title, status=status, detail=detail, action=action)


def response(checks, **extra):
    return dict(ready=not any(c['status'] == 'blocked' for c in checks), checks=checks,
                checked_at=datetime.now(timezone.utc).isoformat(), **extra)


def http_hint(status, *, model=False):
    if status in (401, 403):
        return ('API Key被拒绝或没有模型权限', '核对服务商API Key和模型使用权限') if model else (
            '执行访问码无效、已撤销或没有权限', '重新填写维护者发放的个人执行访问码')
    if status == 429:
        return ('服务商限流或额度不足', '检查服务商额度或稍后重试') if model else (
            '执行队列繁忙', '稍后点击重新检查')
    if status == 404:
        return ('模型或兼容接口地址不正确', '核对模型ID及API Base URL，不要包含/chat/completions') if model else (
            '执行地址不是兼容服务入口', '维护者检查HTTPS转发路径和服务版本')
    return (f'服务返回HTTP {status}', '检查服务状态；持续失败时联系维护者')


async def _model_metadata(value, transport=None):
    async def fetch(selected_transport):
        async with httpx.AsyncClient(transport=selected_transport, trust_env=False, follow_redirects=False, timeout=8) as client:
            async with client.stream('GET', value['base_url']+'/models',
                    headers={'Authorization': 'Bearer '+value['api_key']}) as r:
                status = r.status_code
                if status != 200:
                    return status, None
                raw = bytearray()
                async for chunk in r.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_048_576:
                        raise ValueError('Metadata response too large')
                return status, json.loads(raw)

    async with asyncio.timeout(12):
        try:
            return await fetch(transport)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if transport is not None:
                raise
            return await fetch(httpx.AsyncHTTPTransport(trust_env=False, local_address='0.0.0.0'))


def discover_models(value, *, transport=None):
    try:
        status, data = asyncio.run(_model_metadata(value, transport))
        if status in (404, 405, 501):
            return response([item('model_metadata', '模型服务', 'warning',
                '服务商没有提供模型列表接口，仍可手动填写模型ID', '可点击“测试模型调用”验证，可能产生少量费用')], models=[])
        if status != 200:
            detail, action = http_hint(status, model=True)
            return response([item('model_metadata', '模型服务', 'blocked', detail, action)], models=[])
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError('Unexpected metadata format')
        models = sorted({r['id'] for r in rows if isinstance(r, dict)
                         and isinstance(r.get('id'), str) and 0 < len(r['id']) <= 200
                         and not any(c in r['id'] for c in '\r\n\0')})[:500]
        detail = f'接口连接和鉴权通过，读取到{len(models)}个模型；尚未发送生成请求'
        return response([item('model_metadata', '模型服务', 'pass', detail)], models=models)
    except (TimeoutError, httpx.TimeoutException):
        return response([item('model_metadata', '模型服务', 'blocked', '模型服务连接超时',
                              '检查网络和API地址，稍后重新检查')], models=[])
    except httpx.HTTPError:
        return response([item('model_metadata', '模型服务', 'blocked', '无法建立模型服务的HTTPS连接',
                              '检查API地址、网络和服务商证书')], models=[])
    except (ValueError, UnicodeDecodeError):
        return response([item('model_metadata', '模型服务', 'warning', '模型列表响应不兼容，仍可手动填写ID',
                              '点击“测试模型调用”进一步验证，可能产生少量费用')], models=[])


def check_execution(value, folder, *, runner=None, emit=lambda row: None):
    checks = []
    def add(row):
        checks.append(row)
        emit(row)
    owned = runner is None
    try:
        runner = runner or RemoteRunner('qb-get_factors', url=value['execution_url'], token=value['execution_token'])
        health = runner.request('GET', '/v1/health', seconds=8)
        if health.get('protocol') != PROTOCOL or health.get('status') != 'available':
            add(item('execution', '执行服务与访问码', 'blocked', '服务版本或运行状态不兼容', '维护者更新执行服务后重试'))
            return response(checks)
        add(item('execution', '执行服务与访问码', 'pass', '已连接云端，访问码有效'))
        add(item('isolation', '云端隔离环境', 'checking', '正在实际运行隔离探针，不调用模型'))
        proof = runner.preflight(Path(folder) / 'isolation')
        checks.pop()
        if proof.get('isolation_ready') is True:
            add(item('isolation', '云端隔离环境', 'pass', '真实隔离探针通过；任务启动时仍会重新验证'))
            return response(checks, isolation_verified=True, image=proof.get('image'))
        add(item('isolation', '云端隔离环境', 'blocked', '隔离探针未通过：'+str(proof.get('reason') or '证据未满足要求'),
                 '由维护者检查云端Docker、镜像和挂载权限；用户电脑无需安装Docker'))
    except (IsolationBlocked, ValueError, KeyError, TypeError) as error:
        status = re.search(r'HTTP\s+(\d{3})', str(error))
        detail, action = http_hint(int(status[1])) if status else (
            str(error), '重试；若持续失败，由维护者检查公网HTTPS入口和服务。无需在用户电脑配置Docker或SSH')
        add(item('execution', '执行服务与访问码', 'blocked', detail, action))
        add(item('isolation', '云端隔离环境', 'pending', '执行服务连接通过后自动检查'))
    finally:
        if owned and runner:
            runner.close()
    return response(checks, isolation_verified=False)


class _TestModel(LiveModel):
    http_status = None
    async def _request(self, messages, seconds, *, ipv4_only=False):
        try:
            return await super()._request(messages, seconds, ipv4_only=ipv4_only)
        except httpx.HTTPStatusError as error:
            self.http_status = error.response.status_code
            raise


def test_model(value, root, *, transport=None):
    if value.get('approved') is not True:
        return response([item('model_call', '真实模型调用', 'blocked', '尚未同意使用自己的API额度',
                              '勾选费用说明后，主动点击“测试模型调用”')], model_requests=0)
    from .personal import settings_for_profile
    from dataclasses import replace
    profile = dict(model=value['model'], max_input_tokens=64000,
                   max_output_tokens=min(128, value['max_output_tokens']), price_version=value['price_version'],
                   token_limit_parameter=value['token_parameter'])
    if value.get('model_prices', {}).get(value['model']):
        profile['personal_pricing'] = value['model_prices'][value['model']]
    identifier = 'connection-'+uuid.uuid4().hex
    try:
        settings = replace(settings_for_profile(profile, value['api_key'], value['base_url']), timeout=30)
        model = _TestModel(settings, Ledger(root), transport=transport)
        record = model.call([{'role': 'user', 'content': 'Reply with OK. This is a connection test.'}],
                            run_id=identifier, call_id=identifier, max_calls=1, deadline=time.monotonic()+30)
    except ModelBlocked as error:
        return response([item('model_call', '真实模型调用', 'blocked', str(error),
                              '核对已有未知费用、额度和调用限制后再测试')], model_requests=0)
    folder = Path(root) / 'connection-tests' / identifier
    folder.mkdir(parents=True)
    (folder / 'model-call.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    requests = int(record.get('transport_request_started', False))
    if record['status'] == 'ok':
        check = item('model_call', '真实模型调用', 'pass', '已收到所选模型的真实回复', '测试只验证模型调用，不代表修复或隔离验收')
    elif model.http_status:
        detail, action = http_hint(model.http_status, model=True)
        check = item('model_call', '真实模型调用', 'blocked', detail, action)
    elif record.get('failure_class') == 'cost_unknown':
        held = record.get('cost', {}).get('budget_handling') == 'reserved_upper_bound'
        check = item('model_call', '真实模型调用', 'blocked',
                     '服务商未返回用量，当前请求停止，费用按上限预留' if held else '收到回复但服务商未返回可核对用量，已暂停继续调用',
                     '不重复发送本请求；其他任务可在原金额预算剩余额度内继续' if held else '先核对服务商用量，不自动重试')
    elif record.get('cost', {}).get('status') == 'not_sent':
        check = item('model_call', '真实模型调用', 'blocked', '连接失败，请求未发送，已释放费用预留',
                     '已有限重连一次；检查API地址或网络后重试，无需账单截图')
    else:
        check = item('model_call', '真实模型调用', 'blocked', '请求超时、响应不兼容或连接失败',
                     '核对API地址、模型ID和输出参数；不自动重试，未知用量须先核对')
    return response([check], model_requests=requests, cost=record.get('cost'),
                    reply_excerpt=model.redact(record.get('raw_text', ''))[:200], test_id=identifier)


def check_ledger(root):
    ledger = Ledger(root)
    with sqlite3.connect(ledger.path) as db:
        pending = db.execute("SELECT count(*) FROM calls WHERE status IN ('pending','unknown')").fetchone()[0]
        pending += db.execute("SELECT count(*) FROM cny_calls WHERE status IN ('pending','unknown')").fetchone()[0]
        pending += db.execute("SELECT count(*) FROM personal_calls WHERE status IN ('pending','unknown')").fetchone()[0]
        held = db.execute("SELECT count(*) FROM personal_calls WHERE status='reserved_unknown'").fetchone()[0]
    return item('usage', '调用账本', 'blocked' if pending else 'warning' if held else 'pass',
                f'有{pending}条请求仍进行中或用量未知' if pending else
                f'有{held}条费用待核对，已按上限保留预算' if held else '没有进行中或用量未知的请求',
                '等待请求结算；历史未知需核对后处理，不会自动重试' if pending else
                '仅可在原金额预算与适用累计授权内继续；真实费用不记作零' if held else '')


def _main():
    value = json.loads(sys.stdin.buffer.read(65537))
    if not isinstance(value, dict) or len(json.dumps(value)) > 65536:
        raise ValueError('Invalid desktop check request')
    mode = value.pop('check_mode')
    root = Path(os.environ['CODE_FIX_RUN_ROOT'])
    os.environ['CODE_FIX_PERSONAL_MODE'] = '1'
    os.environ['CODE_FIX_PERSONAL_BASE_URL'] = value.get('base_url', '')
    os.environ['CODE_FIX_EXECUTION_SSH_TUNNEL'] = '1' if value.get('ssh_tunnel') else '0'
    from .providers import validate_url
    if mode not in {'execution', 'prices'} and not validate_url('personal', value.get('base_url', '')):
        raise ValueError('Invalid personal endpoint')
    if mode == 'prices':
        from .personal_pricing import parse_import, catalog
        result = response([], price_catalog=catalog(),
            prices=parse_import(value['price_text'], value.get('price_format', 'json')) if value.get('price_text') else [])
    elif mode == 'test_model':
        os.environ.update(CODE_FIX_PERSONAL_BUDGET='money' if value.get('monetary_budget') else 'usage',
            CODE_FIX_PERSONAL_DAILY_USD=value.get('daily_usd') or '',
            CODE_FIX_PERSONAL_INPUT_PRICE=value.get('input_price') or '',
            CODE_FIX_PERSONAL_OUTPUT_PRICE=value.get('output_price') or '')
        os.environ['CODE_FIX_PERSONAL_DAILY_LIMIT'] = value.get('daily_limit') or ''
        result = test_model(value, root)
    elif mode == 'models':
        result = discover_models(value)
    elif mode in {'execution', 'all'}:
        rows, models = [], []
        if mode == 'all':
            rows.append(check_ledger(root))
            metadata = discover_models(value)
            rows.extend(metadata['checks'])
            models = metadata['models']
            for row in rows:
                print(json.dumps({'check_progress': row}, ensure_ascii=False), flush=True)
        checked = check_execution(value, root / 'connection-checks' / uuid.uuid4().hex,
            emit=lambda row: print(json.dumps({'check_progress': row}, ensure_ascii=False), flush=True))
        result = response(rows+checked['checks'], models=models, isolation_verified=checked.get('isolation_verified', False))
    else:
        raise ValueError('Unknown desktop check mode')
    print(json.dumps({'check_result': result}, ensure_ascii=False), flush=True)


def main():
    try:
        _main()
    except (ModelBlocked, ValueError, KeyError, TypeError, OSError) as exc:
        # Provider bodies and credentials must not appear in process errors.
        from .errors import InputValidationError
        detail = str(exc) if isinstance(exc, InputValidationError) else '配置或本机检查程序未就绪'
        result = response([item('configuration', '连接配置', 'blocked', detail,
                                '核对必填项、费用设置及安装文件后重试')])
        print(json.dumps({'check_result': result}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
