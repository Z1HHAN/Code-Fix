"""Explicitly enabled HTTPS model calls with a durable conservative cost ledger.

No SDK retries or proxy inheritance. Uncertain charges retain the reservation
and block further paid calls until an operator reconciles the ledger.
"""
from dataclasses import dataclass
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

import httpx


class ModelBlocked(ValueError):
    pass


def positive_decimal(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ModelBlocked('费用或时限必须为有限正数') from None


@dataclass(frozen=True)
class LiveSettings:
    api_key: str
    model: str
    base_url: str
    timeout: float
    daily_limit: Decimal
    input_per_million: Decimal
    output_per_million: Decimal
    max_output_tokens: int = 2048
    max_input_tokens: int = 16000
    provider_id: str = 'primary'
    price_version: str = 'operator-environment'
    token_limit_parameter: str = 'max_completion_tokens'

    @classmethod
    def from_env(cls):
        if os.environ.get('CODE_FIX_LIVE_APPROVED') != '1':
            raise ModelBlocked('需队长确认付费额度后设置CODE_FIX_LIVE_APPROVED=1')
        key = os.environ.get('CODE_FIX_API_KEY') or os.environ.get('OPENAI_API_KEY')
        model = os.environ.get('CODE_FIX_MODEL')
        if not key or not model:
            raise ModelBlocked('缺少模型密钥或CODE_FIX_MODEL')
        base = os.environ.get('CODE_FIX_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelBlocked('模型地址必须为无凭证、无查询参数的HTTPS URL')
        timeout = positive_decimal(os.environ.get('CODE_FIX_REQUEST_SECONDS'))
        if timeout > 120:
            raise ModelBlocked('模型单请求时限不能超过120秒')
        return cls(key, model, base, float(timeout),
                   positive_decimal(os.environ.get('CODE_FIX_DAILY_USD')),
                   positive_decimal(os.environ.get('CODE_FIX_INPUT_USD_PER_MILLION')),
                   positive_decimal(os.environ.get('CODE_FIX_OUTPUT_USD_PER_MILLION')))


class Ledger:
    def __init__(self, root):
        self.path = Path(root) / 'model-ledger.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, day TEXT NOT NULL,
                reserved_microusd INTEGER NOT NULL, status TEXT NOT NULL, actual_microusd INTEGER)''')
            # Additive migration preserves earlier cost records, including unknowns.
            columns = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
            for name, default in (('provider_id', 'primary'), ('model', 'legacy-unrecorded'),
                                  ('price_version', 'legacy-unrecorded')):
                if name not in columns:
                    db.execute(f"ALTER TABLE calls ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")

    def reserve(self, call_id, run_id, settings, max_calls, upper_input):
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        amount = int((upper_input * settings.input_per_million + settings.max_output_tokens * settings.output_per_million).to_integral_value(rounding=ROUND_CEILING))
        limit = int(settings.daily_limit * 1_000_000)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM calls WHERE status IN ('pending','unknown')").fetchone():
                raise ModelBlocked('存在进行中或费用未知请求，暂停新付费请求')
            if db.execute('SELECT count(*) FROM calls WHERE run_id=?', (run_id,)).fetchone()[0] >= max_calls:
                raise ModelBlocked('达到本任务模型调用上限')
            used = db.execute('SELECT coalesce(sum(coalesce(actual_microusd,reserved_microusd)),0) FROM calls WHERE day=?', (day,)).fetchone()[0]
            if used + amount > limit:
                raise ModelBlocked('达到队伍每日共享费用上限')
            db.execute('''INSERT INTO calls (call_id,run_id,day,reserved_microusd,status,
                       provider_id,model,price_version) VALUES (?,?,?,?,?,?,?,?)''',
                       (call_id, run_id, day, amount, 'pending', settings.provider_id,
                        settings.model, settings.price_version))

    def settle(self, call_id, usage, settings):
        valid = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens'))
        amount = None
        if valid:
            amount = int((usage['prompt_tokens'] * settings.input_per_million + usage['completion_tokens'] * settings.output_per_million).to_integral_value(rounding=ROUND_CEILING))
        with sqlite3.connect(self.path) as db:
            reserved = db.execute('SELECT reserved_microusd FROM calls WHERE call_id=?', (call_id,)).fetchone()[0]
            if amount is not None and amount > reserved:
                valid = False
            db.execute('UPDATE calls SET status=?,actual_microusd=? WHERE call_id=?',
                       ('known' if valid else 'unknown', amount if valid else None, call_id))
        return {'status': 'estimated' if valid else 'unknown', 'amount_usd': str(Decimal(amount) / 1_000_000) if valid else None,
                'basis': 'operator_configured_rates; not provider invoice'}


    def summary(self, run_id):
        with sqlite3.connect(self.path) as db:
            rows = db.execute('''SELECT provider_id,status,actual_microusd,reserved_microusd
                               FROM calls WHERE run_id=? ORDER BY rowid''', (run_id,)).fetchall()
        providers = {}
        for provider, status, actual, reserved in rows:
            item = providers.setdefault(provider, {'calls': 0, 'estimated_microusd': 0,
                                                  'unsettled_calls': 0, 'reserved_microusd': 0})
            item['calls'] += 1
            item['estimated_microusd'] += actual or 0
            if status != 'known':
                item['unsettled_calls'] += 1
                item['reserved_microusd'] += reserved
        unknown = any(x['unsettled_calls'] for x in providers.values())
        total = sum(x['estimated_microusd'] for x in providers.values())
        return {'status': 'unknown' if unknown else 'estimated' if rows else 'not_called',
                'amount_usd': None if unknown else str(Decimal(total) / 1_000_000),
                'providers': providers, 'basis': 'operator_configured_rates; not provider invoice'}


class LiveModel:
    mode = 'live'

    def __init__(self, settings, ledger, *, transport=None):
        self.settings, self.ledger = settings, ledger
        self.transport = transport
        self.mode = 'live' if transport is None else 'test_transport'

    async def _request(self, messages, seconds):
        # The outer deadline cancels headers AND the entire body stream. A peer
        # sending one byte per read timeout cannot keep this request alive.
        async with asyncio.timeout(seconds):
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                        follow_redirects=False, timeout=seconds) as client:
                async with client.stream('POST', self.settings.base_url + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + self.settings.api_key},
                        json={'model': self.settings.model, 'messages': messages,
                              self.settings.token_limit_parameter: self.settings.max_output_tokens}) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > 1_048_576:
                            raise ValueError('响应超过上限')
                    return json.loads(payload)

    def redact(self, value):
        if isinstance(value, str):
            return value.replace(self.settings.api_key, '[REDACTED]')
        if isinstance(value, list):
            return [self.redact(x) for x in value]
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        return value

    def call(self, messages, *, run_id, call_id, max_calls, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelBlocked('总任务时限已到')
        # UTF-8 bytes plus framing allowance is a conservative token bound.
        upper_input = sum(len(m['content'].encode('utf-8')) + 100 for m in messages) + 100
        if upper_input > self.settings.max_input_tokens:
            raise ModelBlocked('输入过长，拒绝未经计账的截断')
        self.ledger.reserve(call_id, run_id, self.settings, max_calls, upper_input)
        record = {'role': None, 'mode': self.mode, 'model_api_called': False,
                  'model': self.settings.model, 'call_id': call_id, 'status': 'failed',
                  'provider_id': self.settings.provider_id, 'price_version': self.settings.price_version,
                  'usage': None, 'cost': {'status': 'unknown', 'amount_usd': None},
                  'attempts': 1, 'max_attempts': 1, 'failure_class': 'model_transport_error'}
        started = time.monotonic()
        try:
            remaining = min(deadline - time.monotonic(), self.settings.timeout)
            if remaining <= 0:
                raise TimeoutError()
            record['model_api_called'] = self.transport is None
            record['transport_request_started'] = True
            value = asyncio.run(self._request(messages, remaining))
            record['usage'] = value.get('usage')
            record['cost'] = self.ledger.settle(call_id, record['usage'], self.settings)
            content = value['choices'][0]['message']['content']
            if not isinstance(content, str) or not content.strip():
                raise ValueError('缺少响应文本')
            record.update(raw_text=content, status='ok', failure_class=None,
                          provider_model=value.get('model'), provider_response_id=value.get('id'))
            if record['cost']['status'] == 'unknown':
                record.update(status='blocked', failure_class='cost_unknown')
        except Exception as exc:
            if record['cost']['status'] == 'unknown':
                self.ledger.settle(call_id, None, self.settings)
            record['error_type'] = type(exc).__name__
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                record['failure_class'] = 'model_request_timeout'
        finally:
            record['duration_ms'] = round((time.monotonic() - started) * 1000)
        return self.redact(record)
