from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
import time

import httpx
import pytest

from app.errors import InputValidationError
from app.live_model import Ledger, LiveModel, LiveSettings, ModelBlocked
from app.personal_pricing import calculate, catalog, parse_import, validate_price


def price():
    return next(p for p in catalog() if p['model'] == 'qwen-plus' and p['mode'] == 'non_thinking')


def settings(**extra):
    return LiveSettings('fixture-key', 'qwen-plus', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        10, Decimal('5'), None, None, currency='CNY', personal_pricing=price(),
        **extra)


def test_import_normalizes_and_versions_immutable_snapshots():
    p = price()
    assert parse_import(json.dumps(p)) == [p]
    assert validate_price(p) == p
    changed = {**p, 'tiers': [{'max_input_tokens': 1000000, 'input': '1', 'output': '2'}]}
    assert validate_price(changed)['version'] != p['version']
    assert p == price()


@pytest.mark.parametrize('tokens,expected', [(128000, 102400), (128001, 307203), (256001, 1228805)])
def test_entire_input_uses_matching_tier(tokens, expected):
    assert calculate(price(), {'prompt_tokens': tokens, 'completion_tokens': 0})[0] == expected


def test_cache_and_reasoning_are_not_double_counted():
    p = price()
    p['tiers'][0]['cached_input'] = '.2'
    usage = {'prompt_tokens': 100, 'completion_tokens': 50,
        'prompt_tokens_details': {'cached_tokens': 40},
        'completion_tokens_details': {'reasoning_tokens': 30}}
    assert calculate(p, usage) == (156, False)
    assert calculate(price(), usage) == (180, True)


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': True, 'completion_tokens': 0},
    {'prompt_tokens': 1, 'completion_tokens': -1},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 2}},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'prompt_tokens_details': {'cache_creation_input_tokens': 1}}])
def test_incomplete_or_unsupported_usage_is_unknown(usage):
    with pytest.raises(InputValidationError):
        calculate(price(), usage)


def test_csv_price_preview_and_duplicate_rejection():
    text = 'provider,model,region,mode,currency,unit,source_url,verified_at,endpoint_host,max_input_tokens,input,output\n'
    text += 'manual,m,custom,standard,CNY,per_million_tokens,https://example.com/prices,2026-11-11,example.com,1000,2,4\n'
    assert parse_import(text, 'csv')[0]['tiers'][0]['input'] == '2'
    with pytest.raises(InputValidationError):
        parse_import(json.dumps([price(), price()]))


def test_offpeak_reservation_always_covers_peak():
    p = catalog()[-1]
    usage = {'prompt_tokens': 100, 'completion_tokens': 50}
    assert calculate(p, usage, now=datetime(2026, 11, 11, 1, tzinfo=timezone.utc))[0] == 600
    assert calculate(p, usage, now=datetime(2026, 11, 11, 16, tzinfo=timezone.utc))[0] == 300
    assert calculate(p, usage, reserve=True)[0] == 600


def test_unknown_blocks_both_personal_and_legacy_paths(tmp_path):
    ledger = Ledger(tmp_path)
    s = settings()
    ledger.reserve('one', 'run', s, 12, 100)
    assert ledger.settle('one', None, s)['status'] == 'unknown'
    for candidate in [s, replace(s, personal_pricing=None, currency='USD', input_per_million=Decimal(1), output_per_million=Decimal(1))]:
        with pytest.raises(ModelBlocked, match='未知'):
            ledger.reserve('two', 'other', candidate, 12, 100)
    assert ledger.summary('run')['amount_cny'] is None


def test_cumulative_authorization_applies_across_runs(tmp_path):
    ledger = Ledger(tmp_path)
    s = settings(authorization_id='test-batch', authorization_limit=Decimal('.0002'))
    # Reserve a small bounded request, then settle it; next task shares the cap.
    s = replace(s, max_output_tokens=20)
    ledger.reserve('one', 'first', s, 12, 100)
    ledger.settle('one', {'prompt_tokens': 100, 'completion_tokens': 20}, s)
    with pytest.raises(ModelBlocked, match='累计费用授权'):
        ledger.reserve('two', 'second', s, 12, 100)


def test_ledger_uses_saved_price_even_if_settings_change(tmp_path):
    ledger = Ledger(tmp_path)
    s = settings()
    ledger.reserve('one', 'run', s, 12, 100)
    changed = replace(s, personal_pricing=catalog()[0])
    cost = ledger.settle('one', {'prompt_tokens': 100, 'completion_tokens': 20}, changed)
    assert cost['amount_cny'] == '0.00012'
    assert cost['price_version'] == price()['version']
    assert ledger.summary('run')['amount_cny'] == '0.00012'


def test_authentication_error_has_actionable_reason_without_response_body(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text='fixture-key private body'))
    record = LiveModel(settings(), Ledger(tmp_path), transport=transport).call(
        [{'role': 'user', 'content': 'hello'}], run_id='run', call_id='one', max_calls=12, deadline=time.monotonic()+10)
    assert record['http_status'] == 401 and record['status'] == 'failed'
    assert record['reason'] and record['cost']['status'] == 'unknown'
    assert 'fixture-key' not in json.dumps(record) and 'private body' not in json.dumps(record)


def test_daily_limit_prevents_http_request(tmp_path):
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request))
    s = replace(settings(), daily_limit=Decimal('.000001'))
    with pytest.raises(ModelBlocked, match='每日费用'):
        LiveModel(s, Ledger(tmp_path), transport=transport).call(
            [{'role': 'user', 'content': 'hello'}], run_id='run', call_id='one', max_calls=12, deadline=time.monotonic()+10)
    assert requests == []


def test_personal_unknown_can_only_be_reconciled_with_matching_currency_and_receipt(tmp_path):
    from app.reconciliation import reconcile
    ledger, s = Ledger(tmp_path), settings()
    ledger.reserve('one', 'run', s, 12, 100)
    ledger.settle('one', None, s)
    proof = tmp_path / 'invoice.txt'
    proof.write_text('offline reconciliation proof fixture', encoding='utf-8')
    kwargs = dict(call_id='one', amount='.01', evidence=proof, operator='fixture',
                  reason='offline test', no_inflight=True)
    with pytest.raises(ValueError, match='币种'):
        reconcile(ledger, currency='USD', **kwargs)
    receipt = reconcile(ledger, currency='CNY', **kwargs)
    assert receipt['before']['status'] == 'unknown'
    assert ledger.summary('run')['status'] == 'reconciled'
    assert ledger.summary('run')['amount_cny'] == '0.01'
    ledger.reserve('two', 'other', s, 12, 100)


def test_each_model_gets_its_own_price_and_cost(tmp_path):
    ledger, first = Ledger(tmp_path), settings()
    second = replace(first, model='qwen-max', personal_pricing=catalog()[0])
    for call, s in [('one', first), ('two', second)]:
        ledger.reserve(call, 'run', s, 12, 100)
        ledger.settle(call, {'prompt_tokens': 100, 'completion_tokens': 20}, s)
    summary = ledger.summary('run')
    assert summary['currency'] == 'CNY' and summary['amount_cny'] == '0.000552'
    assert {m['model'] for m in summary['models']} == {'qwen-plus', 'qwen-max'}
    assert len({m['price_version'] for m in summary['models']}) == 2
