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

import httpx


class ModelBlocked(ValueError):
    pass


class BudgetBlocked(ModelBlocked):
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
    currency: str = 'USD'
    account_id: str | None = None
    account_limit: Decimal | None = None
    warning_balance: Decimal | None = None
    team_limit: Decimal | None = None
    billable_output_tokens: int | None = None
    request_parameters: dict | None = None
    personal_pricing: dict | None = None
    authorization_id: str | None = None
    authorization_limit: Decimal | None = None

    @classmethod
    def from_env(cls):
        if os.environ.get('CODE_FIX_LIVE_APPROVED') != '1':
            raise ModelBlocked('需队长确认付费额度后设置CODE_FIX_LIVE_APPROVED=1')
        from .providers import validate_url
        provider = os.environ.get('CODE_FIX_PROVIDER_ID')
        base = os.environ.get('CODE_FIX_BASE_URL', '').rstrip('/')
        if not validate_url(provider, base):
            raise ModelBlocked('旧live_env需设置已登记的CODE_FIX_PROVIDER_ID及匹配白名单的CODE_FIX_BASE_URL；请迁移配置')
        key = os.environ.get('CODE_FIX_API_KEY') or os.environ.get('OPENAI_API_KEY')
        model = os.environ.get('CODE_FIX_MODEL')
        if not key or not model:
            raise ModelBlocked('缺少模型密钥或CODE_FIX_MODEL')
        from .redaction import register_secret
        register_secret(key)
        timeout = positive_decimal(os.environ.get('CODE_FIX_REQUEST_SECONDS'))
        if timeout > 120:
            raise ModelBlocked('模型单请求时限不能超过120秒')
        return cls(key, model, base, float(timeout),
                   positive_decimal(os.environ.get('CODE_FIX_DAILY_USD')),
                   positive_decimal(os.environ.get('CODE_FIX_INPUT_USD_PER_MILLION')),
                   positive_decimal(os.environ.get('CODE_FIX_OUTPUT_USD_PER_MILLION')), provider_id=provider)


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
            from .account_budget import AccountBudget
            AccountBudget.initialize(db)
            from .personal_ledger import initialize
            initialize(db)

    def reserve(self, call_id, run_id, settings, max_calls, upper_input):
        if settings.personal_pricing:
            from .personal_ledger import reserve
            return reserve(self.path, call_id, run_id, settings, max_calls, upper_input)
        if settings.currency == 'CNY':
            from .account_budget import AccountBudget
            return AccountBudget(self.path).reserve(call_id, run_id, settings, max_calls, upper_input)
        if settings.currency not in {'USD', 'USAGE'}:
            raise ModelBlocked('未实现的费用币种')
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        amount = (int((upper_input * settings.input_per_million + settings.max_output_tokens * settings.output_per_million).to_integral_value(rounding=ROUND_CEILING))
                  if settings.currency == 'USD' else 0)
        limit = int(settings.daily_limit * 1_000_000) if settings.currency == 'USD' else None
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            if (db.execute("SELECT 1 FROM calls WHERE status IN ('pending','unknown')").fetchone()
                    or db.execute("SELECT 1 FROM cny_calls WHERE status IN ('pending','unknown')").fetchone()
                    or db.execute("SELECT 1 FROM personal_calls WHERE status IN ('pending','unknown')").fetchone()):
                unknown = (db.execute("SELECT 1 FROM calls WHERE status='unknown'").fetchone()
                           or db.execute("SELECT 1 FROM cny_calls WHERE status='unknown'").fetchone()
                           or db.execute("SELECT 1 FROM personal_calls WHERE status='unknown'").fetchone())
                raise ModelBlocked('存在费用未知请求，需人工核对后恢复' if unknown else '存在进行中请求，请等待其结算后重试')
            count = db.execute('SELECT count(*) FROM calls WHERE run_id=?', (run_id,)).fetchone()[0]
            count += db.execute('SELECT count(*) FROM cny_calls WHERE run_id=?', (run_id,)).fetchone()[0]
            count += db.execute('SELECT count(*) FROM personal_calls WHERE run_id=?', (run_id,)).fetchone()[0]
            if count >= max_calls:
                raise ModelBlocked('达到本任务模型调用上限')
            used = db.execute('SELECT coalesce(sum(coalesce(actual_microusd,reserved_microusd)),0) FROM calls WHERE day=?', (day,)).fetchone()[0]
            if limit is not None and used + amount > limit:
                raise ModelBlocked('达到队伍每日共享费用上限')
            db.execute('''INSERT INTO calls (call_id,run_id,day,reserved_microusd,status,
                       provider_id,model,price_version) VALUES (?,?,?,?,?,?,?,?)''',
                       (call_id, run_id, day, amount, 'pending', settings.provider_id,
                        settings.model, settings.price_version))

    def settle(self, call_id, usage, settings):
        if settings.personal_pricing:
            from .personal_ledger import settle
            return settle(self.path, call_id, usage)
        if settings.currency == 'CNY':
            from .account_budget import AccountBudget
            return AccountBudget(self.path).settle(call_id, usage, settings)
        valid = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens'))
        if settings.currency == 'USAGE':
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE calls SET status=?,actual_microusd=NULL WHERE call_id=?',
                           ('unpriced' if valid else 'unknown', call_id))
            return {'status': 'unpriced' if valid else 'unknown', 'amount_usd': None,
                    'usage': usage if valid else None, 'basis': 'no_prices_configured; no_monetary_estimate'}
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
        from .personal_ledger import summary
        personal = summary(self.path, run_id)
        with sqlite3.connect(self.path) as db:
            rows = db.execute('''SELECT provider_id,status,actual_microusd,reserved_microusd
                               FROM calls WHERE run_id=? ORDER BY rowid''', (run_id,)).fetchall()
        from .account_budget import AccountBudget
        cny = AccountBudget(self.path).summary(run_id)
        if personal['calls'] and not rows and not cny['calls']:
            return personal
        if cny['calls'] and not rows and not personal['calls']:
            return cny
        providers = {}
        for provider, status, actual, reserved in rows:
            item = providers.setdefault(provider, {'calls': 0, 'estimated_microusd': 0,
                                                  'unsettled_calls': 0, 'reserved_microusd': 0})
            item['calls'] += 1
            item['estimated_microusd'] += actual or 0
            if status in {'pending', 'unknown'}:
                item['unsettled_calls'] += 1
                item['reserved_microusd'] += reserved
        unknown = any(x['unsettled_calls'] for x in providers.values())
        unpriced = any(row[1] == 'unpriced' for row in rows)
        total = sum(x['estimated_microusd'] for x in providers.values())
        result = {'status': 'unknown' if unknown else 'unpriced' if unpriced else 'estimated' if rows else 'not_called',
                'amount_usd': None if unknown or unpriced else str(Decimal(total) / 1_000_000),
                'providers': providers, 'basis': 'operator_configured_rates; not provider invoice'}
        if cny['calls']:
            result = {'status': 'unknown' if unknown or cny['status'] == 'unknown' else 'mixed_currencies',
                      'amount_usd': result['amount_usd'], 'amount_cny': cny['amount_cny'],
                      'budget': cny['budget'], 'basis': 'separate_currencies_no_conversion'}
        if personal['calls']:
            return {'status': 'unknown', 'amount_cny': None, 'amount_usd': None,
                    'components': [result, personal], 'basis': 'mixed_legacy_and_personal; inspect_components'}
        return result


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
                              self.settings.token_limit_parameter: self.settings.max_output_tokens,
                              **(self.settings.request_parameters or {})}) as response:
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
        try:
            self.ledger.reserve(call_id, run_id, self.settings, max_calls, upper_input)
        except ModelBlocked as exc:
            raise BudgetBlocked(str(exc)) from None
        record = {'role': None, 'mode': self.mode, 'model_api_called': False,
                  'model': self.settings.model, 'call_id': call_id, 'status': 'failed',
                  'provider_id': self.settings.provider_id, 'price_version': self.settings.price_version,
                  'usage': None, 'cost': {'status': 'unknown', 'amount_usd': None},
                  'attempts': 1, 'max_attempts': 1, 'transport_request_started': False,
                  'failure_class': 'model_transport_error'}
        record['budget_check'] = {'status': 'ok', 'reason': '调用次数、未结算请求及适用费用上限检查通过，已预留'}
        started = time.monotonic()
        settled = False
        try:
            remaining = min(deadline - time.monotonic(), self.settings.timeout)
            if remaining <= 0:
                raise TimeoutError()
            record['model_api_called'] = self.transport is None
            record['transport_request_started'] = True
            value = asyncio.run(self._request(messages, remaining))
            if not isinstance(value, dict):
                raise ValueError('响应根节点必须是对象')
            record['usage'] = value.get('usage')
            record['cost'] = self.ledger.settle(call_id, record['usage'], self.settings)
            settled = True
            record.update(provider_model=value.get('model'), provider_response_id=value.get('id'))
            # Unknown usage has priority over content validation: it must never
            # become a format failure that automatically sends another request.
            if record['cost']['status'] == 'unknown':
                record.update(status='blocked', failure_class='cost_unknown',
                              reason='服务商用量或费用未知，须先核对，停止后续调用')
            else:
                choice = value['choices'][0]
                finish = choice.get('finish_reason')
                record['finish_reason'] = (finish if isinstance(finish, str) and finish in {
                    'stop', 'length', 'content_filter', 'tool_calls', 'function_call'} else None if finish is None else 'other')
                content = choice['message'].get('content')
                if content is None or isinstance(content, str) and not content.strip():
                    details = record['usage'].get('completion_tokens_details')
                    details = details if isinstance(details, dict) else {}
                    counts = {name: details[name] if type(details.get(name)) is int and details[name] >= 0 else None
                              for name in ('reasoning_tokens', 'text_tokens')}
                    record.update(status='failed', failure_class='invalid_model_output', error_type='EmptyModelOutput',
                        reason=('模型没有返回正文；输出上限为' + str(self.settings.max_output_tokens)
                            + ('，服务商标记输出已截断' if finish == 'length' else '')
                            + '。请在剩余轮次内直接给出符合协议的JSON，减少解释。'),
                        output_diagnostics={'kind': 'reasoning_budget_exhausted' if counts['reasoning_tokens'] == self.settings.max_output_tokens and counts['text_tokens'] == 0 else 'empty_content',
                            'empty_content': True, 'max_output_tokens': self.settings.max_output_tokens, **counts,
                            'suggested_action': '在模型与配置中检查该角色输出上限；支持时限制思考，或选择更大的已批准输出上限。'})
                elif not isinstance(content, str):
                    raise ValueError('响应文本类型不支持')
                elif finish == 'length':
                    record.update(raw_text=content, status='failed', failure_class='invalid_model_output',
                        error_type='TruncatedModelOutput',
                        reason='服务商标记输出达到长度上限，正文可能不完整；本次不应用修改，在剩余额度内重新输出完整协议JSON。',
                        output_diagnostics={'kind': 'truncated_output', 'empty_content': False,
                            'max_output_tokens': self.settings.max_output_tokens,
                            'suggested_action': '检查该角色输出上限；可在模型登记范围内增加长度，避免长解释占用代码空间。'})
                else:
                    record.update(raw_text=content, status='ok', failure_class=None)
        except Exception as exc:
            if not settled:
                usage = None if record['transport_request_started'] else {'prompt_tokens': 0, 'completion_tokens': 0}
                record['cost'] = self.ledger.settle(call_id, usage, self.settings)
            record['error_type'] = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                record['http_status'] = exc.response.status_code
                from .connection_checks import http_hint
                detail, action = http_hint(exc.response.status_code, model=True)
                record['reason'] = detail + '；' + action
            elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                record['reason'] = '请求超过时限；用量未知时先核对账单，不自动重试'
                record['failure_class'] = ('model_request_timeout' if record['transport_request_started'] else 'model_deadline_before_send')
            elif isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
                record['failure_class'] = 'invalid_model_response'
        finally:
            record['duration_ms'] = round((time.monotonic() - started) * 1000)
        return self.redact(record)
