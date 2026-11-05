"""Native CNY account budgets. Values are project estimates, never live balances."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import hmac
import json
import secrets
import sqlite3

from .live_model import ModelBlocked


def micro(value):
    return int((Decimal(value) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def yuan(value):
    return str(Decimal(value) / 1_000_000)


class AccountBudget:
    def __init__(self, path):
        self.path = path

    @staticmethod
    def initialize(db):
        db.execute('''CREATE TABLE IF NOT EXISTS account_limits (
            account_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL,
            authorized_microcny INTEGER NOT NULL, warning_microcny INTEGER NOT NULL,
            team_microcny INTEGER NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS cny_calls (
            call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, account_id TEXT NOT NULL,
            provider_id TEXT NOT NULL, model TEXT NOT NULL, price_version TEXT NOT NULL,
            created_at TEXT NOT NULL, reserved_microcny INTEGER NOT NULL,
            actual_microcny INTEGER, status TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS budget_alerts (
            account_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(account_id,kind))''')
        db.execute('''CREATE TABLE IF NOT EXISTS account_keys (
            provider_id TEXT NOT NULL, fingerprint TEXT NOT NULL, account_id TEXT NOT NULL,
            PRIMARY KEY(provider_id,fingerprint))''')
        db.execute('''CREATE TABLE IF NOT EXISTS ledger_meta (
            name TEXT PRIMARY KEY, value TEXT NOT NULL)''')

    @staticmethod
    def _salt(db):
        row = db.execute("SELECT value FROM ledger_meta WHERE name='key_fingerprint_salt'").fetchone()
        if row is None:
            db.execute("INSERT OR IGNORE INTO ledger_meta VALUES ('key_fingerprint_salt', ?)",
                       (secrets.token_bytes(32).hex(),))
            row = db.execute("SELECT value FROM ledger_meta WHERE name='key_fingerprint_salt'").fetchone()
        return bytes.fromhex(row[0])

    @classmethod
    def _fingerprint(cls, db, settings):
        """本账本随机盐的 HMAC 截断值：不落无盐 sha256，跨账本不可比对。"""
        return hmac.new(cls._salt(db), settings.api_key.encode(), hashlib.sha256).hexdigest()[:32]

    @classmethod
    def _register(cls, db, settings):
        fingerprint = cls._fingerprint(db, settings)
        # 兼容旧账本里已存在的无盐 sha256 指纹：命中后升级为带盐值，守卫强度不变。
        legacy = hashlib.sha256(settings.api_key.encode()).hexdigest()
        rows = db.execute('SELECT account_id, fingerprint FROM account_keys '
                          'WHERE provider_id=? AND fingerprint IN (?,?)',
                          (settings.provider_id, fingerprint, legacy)).fetchall()
        if any(row[0] != settings.account_id for row in rows):
            raise ModelBlocked('同一凭证已记入其他账号，不能重新命名重置额度')
        for _account_id, old in rows:
            if old != fingerprint:
                db.execute('DELETE FROM account_keys WHERE provider_id=? AND fingerprint=?',
                           (settings.provider_id, old))
        db.execute('INSERT OR IGNORE INTO account_keys VALUES (?,?,?)',
                   (settings.provider_id, fingerprint, settings.account_id))
        values = (settings.account_id, settings.provider_id, micro(settings.account_limit),
                  micro(settings.warning_balance), micro(settings.team_limit))
        db.execute('INSERT OR IGNORE INTO account_limits VALUES (?,?,?,?,?)', values)
        row = db.execute('SELECT * FROM account_limits WHERE account_id=?', (settings.account_id,)).fetchone()
        if tuple(row) != values:
            raise ModelBlocked('账号额度配置与已有账本不一致，需人工复核，不能自动重置额度')
        if db.execute('SELECT 1 FROM account_limits WHERE team_microcny<>?', (values[-1],)).fetchone():
            raise ModelBlocked('队伍人民币总额度配置不一致')

    @staticmethod
    def _alert(db, account_id, kind, **payload):
        value = {'account_id': account_id, 'kind': kind, 'currency': 'CNY',
                 'basis': 'project_ledger_estimate_not_platform_balance', **payload}
        db.execute('INSERT OR IGNORE INTO budget_alerts VALUES (?,?,?,?)',
                   (account_id, kind, datetime.now(timezone.utc).isoformat(), json.dumps(value, ensure_ascii=False)))

    def reserve(self, call_id, run_id, settings, max_calls, upper_input):
        output = settings.billable_output_tokens or settings.max_output_tokens
        amount = int((upper_input * settings.input_per_million + output * settings.output_per_million)
                     .to_integral_value(rounding=ROUND_CEILING))
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            self._register(db, settings)
            if (db.execute("SELECT 1 FROM calls WHERE status IN ('pending','unknown')").fetchone()
                    or db.execute("SELECT 1 FROM cny_calls WHERE status IN ('pending','unknown')").fetchone()):
                raise ModelBlocked('存在进行中或费用未知请求，暂停所有账号及币种的新付费请求')
            count = db.execute('SELECT count(*) FROM calls WHERE run_id=?', (run_id,)).fetchone()[0]
            count += db.execute('SELECT count(*) FROM cny_calls WHERE run_id=?', (run_id,)).fetchone()[0]
            if count >= max_calls:
                raise ModelBlocked('达到本任务模型调用上限')
            used = db.execute('SELECT coalesce(sum(coalesce(actual_microcny,reserved_microcny)),0) FROM cny_calls WHERE account_id=?',
                              (settings.account_id,)).fetchone()[0]
            total = db.execute('SELECT coalesce(sum(coalesce(actual_microcny,reserved_microcny)),0) FROM cny_calls').fetchone()[0]
            if used + amount > micro(settings.account_limit):
                raise ModelBlocked('本账号累计人民币额度不足；不自动切换账号')
            if total + amount > micro(settings.team_limit):
                raise ModelBlocked('达到队伍累计人民币共享上限')
            db.execute('INSERT INTO cny_calls VALUES (?,?,?,?,?,?,?,?,NULL,?)',
                       (call_id, run_id, settings.account_id, settings.provider_id, settings.model,
                        settings.price_version, datetime.now(timezone.utc).isoformat(), amount, 'pending'))
            remaining = micro(settings.account_limit) - used - amount
            if remaining <= micro(settings.warning_balance):
                self._alert(db, settings.account_id, 'low_balance', remaining_cny=yuan(remaining),
                            threshold_cny=str(settings.warning_balance),
                            message='本项目估算剩余额度已降至提醒阈值，包含当前预留，请核对平台控制台')

    def settle(self, call_id, usage, settings):
        valid = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                              for k in ('prompt_tokens', 'completion_tokens'))
        amount = int((usage['prompt_tokens'] * settings.input_per_million + usage['completion_tokens'] * settings.output_per_million)
                     .to_integral_value(rounding=ROUND_CEILING)) if valid else None
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT reserved_microcny,status FROM cny_calls WHERE call_id=?', (call_id,)).fetchone()
            if row is None or row[1] != 'pending':
                raise ModelBlocked('费用记录不存在或已经结算，拒绝覆盖')
            valid = valid and amount <= row[0]
            db.execute('UPDATE cny_calls SET status=?,actual_microcny=? WHERE call_id=?',
                       ('known' if valid else 'unknown', amount if valid else None, call_id))
            if not valid:
                self._alert(db, settings.account_id, 'cost_unknown',
                            message='存在费用未知请求，所有账号暂停付费，待人工核对账单')
        return {'status': 'estimated' if valid else 'unknown', 'currency': 'CNY',
                'amount_cny': yuan(amount) if valid else None, 'amount_usd': None,
                'account_id': settings.account_id, 'basis': 'configured_CNY_rates_not_provider_invoice',
                'budget': self.snapshot()}

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            accounts = []
            for aid, provider, cap, warning, team in db.execute('SELECT * FROM account_limits ORDER BY account_id'):
                used, reserved, unknown = db.execute('''SELECT
                    coalesce(sum(actual_microcny),0),
                    coalesce(sum(CASE WHEN status<>'known' THEN reserved_microcny ELSE 0 END),0),
                    coalesce(sum(CASE WHEN status<>'known' THEN 1 ELSE 0 END),0)
                    FROM cny_calls WHERE account_id=?''', (aid,)).fetchone()
                remaining = cap - used - reserved
                accounts.append({'account_id': aid, 'provider_id': provider, 'currency': 'CNY',
                                 'authorized_cny': yuan(cap), 'estimated_spent_cny': yuan(used),
                                 'reserved_cny': yuan(reserved), 'unsettled_calls': unknown,
                                 'estimated_remaining_cny': None if unknown else yuan(remaining),
                                 'remaining_after_reservations_cny': yuan(remaining),
                                 'warning_threshold_cny': yuan(warning), 'low_balance': remaining <= warning,
                                 'status': 'unknown' if unknown else 'estimated'})
            alerts = [json.loads(row[0]) | {'created_at': row[1]} for row in
                      db.execute('SELECT payload,created_at FROM budget_alerts ORDER BY created_at')]
            blocked = bool(db.execute("SELECT 1 FROM cny_calls WHERE status IN ('pending','unknown')").fetchone()
                           or db.execute("SELECT 1 FROM calls WHERE status IN ('pending','unknown')").fetchone())
        return {'currency': 'CNY', 'basis': 'project_ledger_estimate_not_platform_balance',
                'accounts': accounts, 'alerts': alerts, 'paid_calls_blocked': blocked}

    def summary(self, run_id):
        with sqlite3.connect(self.path) as db:
            count, unknown, amount = db.execute('''SELECT count(*),
                coalesce(sum(CASE WHEN status<>'known' THEN 1 ELSE 0 END),0),
                coalesce(sum(actual_microcny),0) FROM cny_calls WHERE run_id=?''', (run_id,)).fetchone()
        return {'calls': count, 'status': 'unknown' if unknown else 'estimated' if count else 'not_called',
                'currency': 'CNY', 'amount_cny': None if unknown else yuan(amount),
                'amount_usd': None, 'budget': self.snapshot()}
