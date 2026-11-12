"""Personal per-model estimates, separate from legacy team accounts."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from .live_model import ModelBlocked
from .personal_pricing import calculate
from .errors import InputValidationError


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS personal_calls (
        call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, day TEXT NOT NULL,
        created_at TEXT NOT NULL, currency TEXT NOT NULL, model TEXT NOT NULL,
        price_json TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER,
        status TEXT NOT NULL, usage_json TEXT, upper_estimate INTEGER NOT NULL DEFAULT 0,
        authorization_id TEXT)''')


def reserve(path, call_id, run_id, settings, max_calls, upper_input):
    amount, _ = calculate(settings.personal_pricing, {'prompt_tokens': upper_input,
        'completion_tokens': settings.billable_output_tokens or settings.max_output_tokens}, reserve=True)
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        for table in ('calls', 'cny_calls', 'personal_calls'):
            if db.execute(f"SELECT 1 FROM {table} WHERE status IN ('pending','unknown') LIMIT 1").fetchone():
                raise ModelBlocked('存在进行中或费用未知请求，需先结算或核对')
        count = sum(db.execute(f'SELECT count(*) FROM {table} WHERE run_id=?', (run_id,)).fetchone()[0]
                    for table in ('calls', 'cny_calls', 'personal_calls'))
        if count >= max_calls:
            raise ModelBlocked('达到本任务模型调用上限')
        used = db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM personal_calls WHERE day=? AND currency=?',
                          (day, settings.currency)).fetchone()[0]
        if used + amount > int(settings.daily_limit * 1000000):
            raise ModelBlocked('达到个人人民币/美元每日费用上限')
        if settings.authorization_id:
            if settings.currency != 'CNY' or settings.authorization_limit is None:
                raise ModelBlocked('本轮人民币授权仅可用于人民币计价模型')
            used = db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM personal_calls WHERE authorization_id=?',
                              (settings.authorization_id,)).fetchone()[0]
            if used + amount > int(settings.authorization_limit * 1000000):
                raise ModelBlocked('达到本轮累计费用授权上限')
        db.execute('INSERT INTO personal_calls (call_id,run_id,day,created_at,currency,model,price_json,reserved,status,authorization_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (call_id, run_id, day, datetime.now(timezone.utc).isoformat(), settings.currency, settings.model,
             json.dumps(settings.personal_pricing), amount, 'pending', settings.authorization_id))


def settle(path, call_id, usage):
    with sqlite3.connect(path, timeout=10) as db:
        row = db.execute('SELECT price_json,reserved,created_at,currency FROM personal_calls WHERE call_id=?', (call_id,)).fetchone()
        price, reserved, created, currency = row
        amount, upper = None, False
        try:
            amount, upper = calculate(json.loads(price), usage, now=datetime.fromisoformat(created))
            if amount > reserved:
                amount = None
        except (InputValidationError, ValueError, TypeError):
            pass
        db.execute('UPDATE personal_calls SET actual=?,status=?,usage_json=?,upper_estimate=? WHERE call_id=?',
                   (amount, 'estimated' if amount is not None else 'unknown', json.dumps(usage), int(upper), call_id))
    return {'status': 'estimated' if amount is not None else 'unknown',
            'amount_' + currency.lower(): str(Decimal(amount) / 1000000) if amount is not None else None,
            'currency': currency, 'price_version': json.loads(price)['version'],
            'upper_estimate': upper, 'basis': 'versioned_model_rates; not_provider_invoice'}


def summary(path, run_id):
    with sqlite3.connect(path) as db:
        rows = db.execute('SELECT currency,status,actual,reserved,model,price_json,upper_estimate FROM personal_calls WHERE run_id=?', (run_id,)).fetchall()
    result = {'calls': len(rows), 'status': 'not_called', 'basis': 'versioned_model_rates; not_provider_invoice', 'models': []}
    if not rows:
        return result
    unknown = any(row[1] in {'pending', 'unknown'} for row in rows)
    reconciled = all(row[1] == 'reconciled' for row in rows)
    result['status'] = 'unknown' if unknown else 'reconciled' if reconciled else 'estimated'
    if reconciled:
        result['basis'] = 'operator_reconciled_with_evidence_receipts'
    currencies = {r[0] for r in rows}
    if len(currencies) == 1:
        result['currency'] = next(iter(currencies))
    for currency in currencies:
        matching = [r for r in rows if r[0] == currency]
        result['amount_' + currency.lower()] = None if any(r[2] is None for r in matching) else str(Decimal(sum(r[2] for r in matching)) / 1000000)
        result['reserved_' + currency.lower()] = str(Decimal(sum(r[3] for r in matching if r[1] in {'pending','unknown'})) / 1000000)
    for row in rows:
        result['models'].append({'model': row[4], 'currency': row[0], 'status': row[1],
                                'price_version': json.loads(row[5])['version'], 'upper_estimate': bool(row[6])})
    return result
