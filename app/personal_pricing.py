"""Versioned personal prices. Decimal arithmetic; never provider invoices."""
import csv
import io
import json
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from .errors import InputValidationError
from .role_config import digest

SOURCE = 'https://help.aliyun.com/zh/model-studio/model-pricing'


def number(value):
    try:
        d = Decimal(str(value))
        if d.is_finite() and 0 <= d <= 1000000:
            return d
    except InvalidOperation:
        pass
    raise InputValidationError('单价须为有限非负数，单位为每百万Token')


def validate_price(value):
    fields = {'provider', 'model', 'region', 'mode', 'currency', 'unit', 'source_url',
              'verified_at', 'tiers', 'endpoint_host', 'version', 'offpeak_discount'}
    if not isinstance(value, dict) or set(value) - fields:
        raise InputValidationError('价格字段无效')
    p = {k: v for k, v in value.items() if k != 'version'}
    for k in ('provider', 'model', 'region', 'mode', 'source_url', 'verified_at', 'endpoint_host'):
        if not isinstance(p.get(k), str) or not 1 <= len(p[k]) <= 2048 or any(c in p[k] for c in '\r\n\0'):
            raise InputValidationError('价格缺少字段：' + k)
    if p.get('currency') not in {'CNY', 'USD'} or p.get('unit') != 'per_million_tokens':
        raise InputValidationError('价格币种须为CNY/USD，单位须为per_million_tokens')
    if urlsplit(p['source_url']).scheme != 'https' or not urlsplit(p['source_url']).hostname or urlsplit(p['source_url']).username:
        raise InputValidationError('价格来源须为无凭据的HTTPS链接')
    try:
        datetime.fromisoformat(p['verified_at'])
    except ValueError:
        raise InputValidationError('价格核验日期须为ISO日期') from None
    if not isinstance(p.get('tiers'), list) or not 1 <= len(p['tiers']) <= 20:
        raise InputValidationError('价格须包含1至20个输入长度阶梯')
    if p['mode'] not in {'thinking', 'non_thinking', 'standard'}:
        raise InputValidationError('价格模式须为thinking、non_thinking或standard')
    previous = 0
    tiers = []
    for tier in p['tiers']:
        if not isinstance(tier, dict) or set(tier) - {'max_input_tokens', 'input', 'output', 'cached_input'}:
            raise InputValidationError('阶梯价格字段无效')
        top = tier.get('max_input_tokens')
        if type(top) is not int or not previous < top <= 10000000:
            raise InputValidationError('输入阶梯须严格递增且覆盖使用范围')
        previous = top
        t = {'max_input_tokens': top, 'input': str(number(tier.get('input'))), 'output': str(number(tier.get('output')))}
        if tier.get('cached_input') is not None:
            t['cached_input'] = str(number(tier['cached_input']))
            if number(t['cached_input']) > number(t['input']):
                raise InputValidationError('缓存命中单价不能超过标准输入单价')
        tiers.append(t)
    p['tiers'] = tiers
    if 'offpeak_discount' in p and not 0 < number(p['offpeak_discount']) <= 1:
        raise InputValidationError('闲时折扣须大于0且不超过1')
    p['version'] = 'price-' + digest(p)
    return p


def parse_import(text, format='json'):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 65536:
        raise InputValidationError('价格导入最多64KiB')
    if format == 'json':
        rows = json.loads(text)
        rows = rows if isinstance(rows, list) else [rows]
    elif format == 'csv':
        grouped = {}
        for row in csv.DictReader(io.StringIO(text)):
            key = tuple(row.get(k) for k in ('provider', 'model', 'region', 'mode'))
            p = grouped.setdefault(key, {k: v for k, v in row.items() if k not in {'max_input_tokens', 'input', 'output', 'cached_input'}} | {'tiers': []})
            p['tiers'].append({'max_input_tokens': int(row['max_input_tokens']), 'input': row['input'],
                               'output': row['output'], **({'cached_input': row['cached_input']} if row.get('cached_input') else {})})
        rows = list(grouped.values())
    else:
        raise InputValidationError('价格格式须为json或csv')
    if not 1 <= len(rows) <= 12:
        raise InputValidationError('每次导入1至12个模型价格')
    result = [validate_price(p) for p in rows]
    if len({p['model'] for p in result}) != len(result):
        raise InputValidationError('同次导入同模型须选择唯一地域和模式')
    return result


def catalog():
    result = []
    for model, mode, rates in [
        ('qwen-max', 'non_thinking', [(1000000, '2.4', '9.6')]),
        ('qwen-plus', 'non_thinking', [(128000, '.8', '2'), (256000, '2.4', '20'), (1000000, '4.8', '48')]),
        ('qwen-plus', 'thinking', [(128000, '.8', '8'), (256000, '2.4', '24'), (1000000, '4.8', '64')]),
        ('qwen3.5-plus', 'non_thinking', [(128000, '.8', '4.8'), (256000, '2', '12'), (1000000, '4', '24')]),
        ('deepseek-v4.1-flash-1017', 'thinking', [(1000000, '2', '8')]),
    ]:
        p = dict(provider='aliyun-bailian', model=model, region='cn-beijing', mode=mode,
                 currency='CNY', unit='per_million_tokens', source_url=SOURCE, verified_at='2026-11-11',
                 endpoint_host='dashscope.aliyuncs.com',
                 tiers=[dict(max_input_tokens=n, input=i, output=o) for n, i, o in rates])
        if model.startswith('deepseek-v4.1'):
            p['offpeak_discount'] = '0.5'
        result.append(validate_price(p))
    # The user's Qianwen AI platform endpoint has its own published catalog.
    # Exact host/model matching keeps these separate from legacy Bailian rates.
    for old in list(result):
        model = old['model']
        page = {'qwen-max': 'qwen-max', 'qwen-plus': 'qwen-plus',
                'qwen3.5-plus': 'qwen3-5-plus', 'deepseek-v4.1-flash-1017': 'deepseek-v4-1-flash'}[model]
        p = {**old, 'provider': 'qianwen-ai', 'region': 'platform-standard',
             'endpoint_host': 'maas.qianwenaiapi.com',
             'source_url': 'https://platform.qianwenai.com/docs/models/' + page,
             'tiers': [dict(t) for t in old['tiers']]}
        if model == 'qwen-max':
            p['tiers'][0].update(max_input_tokens=30720, cached_input='0.48')
        elif model == 'qwen-plus':
            for t, cached in zip(p['tiers'], ['0.16', '0.48', '0.96']):
                t['cached_input'] = cached
        elif model.startswith('deepseek-v4.1'):
            p['tiers'][0]['cached_input'] = '0.2'
        result.append(validate_price(p))
    return result


def calculate(price, usage, *, reserve=False, now=None):
    p = validate_price(price)
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens')):
        raise InputValidationError('服务商没有返回有效的输入和输出Token计数')
    inputs, outputs = usage['prompt_tokens'], usage['completion_tokens']
    if inputs > p['tiers'][-1]['max_input_tokens']:
        raise InputValidationError('Token超过价格目录覆盖范围')
    details = usage.get('prompt_tokens_details') or {}
    if not isinstance(details, dict):
        raise InputValidationError('缓存Token字段无效')
    cached = details.get('cached_tokens', 0)
    if type(cached) is not int or not 0 <= cached <= inputs:
        raise InputValidationError('缓存Token计数超出输入范围')
    # No explicit-cache creation is requested by this application's transport.
    if details.get('cache_creation_input_tokens', 0):
        raise InputValidationError('显式缓存创建费用尚未登记，费用待核对')
    eligible = [t for t in p['tiers'] if t['max_input_tokens'] >= inputs]
    tier = eligible[0]
    # Reservation covers every lower tier too, even manually imported nonmonotonic prices.
    if reserve:
        eligible = [t for t in p['tiers'] if t['max_input_tokens'] <= tier['max_input_tokens']]
        amount = max(inputs * number(t['input']) + outputs * number(t['output']) for t in eligible)
    else:
        amount = ((inputs-cached) * number(tier['input'])
                  + cached * number(tier.get('cached_input', tier['input'])) + outputs * number(tier['output']))
        hour = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8))).hour
        if 'offpeak_discount' in p and (hour < 8 or hour >= 22):
            amount *= number(p['offpeak_discount'])
    return int(amount.to_integral_value(rounding=ROUND_CEILING)), bool(cached and 'cached_input' not in tier)
