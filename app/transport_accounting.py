"""Sanitized transport evidence and conservative, auditable failure settlement."""
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
import json
import hashlib
from pathlib import Path
import sqlite3

import httpx

ACTIVE_TRACE = ContextVar('codefix_transport_trace', default=None)
HELD = 'reserved_unknown'


class TransportTrace:
    def __init__(self, *, standard_transport=False):
        self.events = []
        self.send_started = False
        self.response_received = False
        self.standard_transport = standard_transport

    async def callback(self, name, info):
        # Never persist info: it can contain request objects and credentials.
        if not isinstance(name, str):
            return
        if name.startswith(('connection.', 'http11.', 'http2.')):
            if len(self.events) < 64:
                self.events.append(name[:100])
            if '.send_request_headers.' in name or '.send_request_body.' in name:
                self.send_started = True
            if name.endswith('receive_response_headers.complete'):
                self.response_received = True

    def proof(self, error=None):
        connect = isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
        trusted = self.standard_transport or any(n.startswith('connection.') for n in self.events)
        not_sent = bool(connect and trusted and not self.send_started and not self.response_received)
        return {'version': 'transport-v1', 'outcome': 'not_sent' if not_sent else 'possibly_sent',
                'error_type': type(error).__name__ if error else None,
                'send_started': self.send_started, 'response_received': self.response_received,
                'events': list(self.events), 'standard_transport': self.standard_transport}


def settle_failure(ledger, call_id, settings, proof):
    """Only new witnessed failures enter here. Old unknowns are never migrated."""
    not_sent = proof.get('outcome') == 'not_sent'
    bounded = (proof.get('outcome') in {'possibly_sent', 'response_missing_usage'}
               and settings.personal_pricing is not None and settings.currency == 'CNY'
               and settings.daily_limit is not None and settings.daily_limit.is_finite()
               and settings.daily_limit > 0
               and (not settings.authorization_id or settings.authorization_limit is not None))
    if not not_sent and not bounded:
        return ledger.settle(call_id, None, settings)
    personal = settings.personal_pricing is not None
    table, actual_column = (('personal_calls', 'actual') if personal else
                           ('cny_calls', 'actual_microcny') if settings.currency == 'CNY' else
                           ('calls', 'actual_microusd'))
    with sqlite3.connect(ledger.path, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        cursor = db.execute(f'SELECT * FROM {table} WHERE call_id=?', (call_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError('请求不存在')
        before = dict(zip((c[0] for c in cursor.description), row))
        if before['status'] != 'pending':
            raise ValueError('只能自动处理本次尚未结算的请求，历史未知不得覆盖')
        if bounded and (before['authorization_id'] != settings.authorization_id or before['reserved'] <= 0):
            raise ValueError('预留与累计授权不匹配')
        state = ('not_sent' if personal else 'known') if not_sent else HELD
        db.execute(f'UPDATE {table} SET status=?,{actual_column}=? WHERE call_id=?',
                   (state, 0 if not_sent else None, call_id))
        db.execute('CREATE TABLE IF NOT EXISTS transport_settlements (call_id TEXT PRIMARY KEY,receipt TEXT NOT NULL)')
        receipt = {'call_id': call_id, 'before': before, 'after_status': state, 'transport': proof,
                   'created_at': datetime.now(timezone.utc).isoformat(),
                   'basis': 'local_transport_proof' if not_sent else 'reservation_retained_not_provider_invoice'}
        db.execute('INSERT INTO transport_settlements VALUES (?,?)', (call_id, json.dumps(receipt)))
    reserved = before.get('reserved', before.get('reserved_microcny', before.get('reserved_microusd', 0)))
    currency = settings.currency.lower()
    return {'status': 'not_sent' if not_sent else state, 'currency': settings.currency,
            'amount_' + currency: '0' if not_sent else None,
            'reserved_' + currency: '0' if not_sent else str(Decimal(reserved)/1000000),
            'budget_handling': 'released_not_sent' if not_sent else 'reserved_upper_bound',
            'budget_scope': 'cumulative_authorization' if settings.authorization_id else 'personal_daily',
            'basis': receipt['basis'], 'price_version': json.loads(before['price_json'])['version'] if personal else settings.price_version}


def carry_unknown_reservation(ledger, *, call_id, evidence, operator, reason):
    """Explicit operator action: retain a historical transport reservation, never infer zero.

    This is not run at startup and does not claim to reconstruct send evidence.
    Returned/invalid usage, missing prices, and pending calls remain strict stops.
    """
    path = Path(evidence)
    if not operator.strip() or not reason.strip() or path.is_symlink() or not path.is_file() or path.stat().st_size > 2**20:
        raise ValueError('需要操作人、原因及原始调用证据')
    raw = path.read_bytes(); record = json.loads(raw)
    if (record.get('call_id') != call_id or record.get('usage') is not None
            or record.get('error_type') not in {'ConnectError','ConnectTimeout','ReadError','ReadTimeout','WriteError','WriteTimeout','PoolTimeout'}):
        raise ValueError('仅允许有原始网络异常且无用量的请求转挂预留')
    with sqlite3.connect(ledger.path, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        for table in ('calls','cny_calls','personal_calls'):
            if db.execute(f"SELECT 1 FROM {table} WHERE status='pending'").fetchone():
                raise ValueError('存在在途请求，不能转挂')
        cursor=db.execute('SELECT * FROM personal_calls WHERE call_id=?',(call_id,))
        row=cursor.fetchone()
        if row is None:raise ValueError('个人请求不存在')
        before=dict(zip((c[0] for c in cursor.description),row))
        from .personal_pricing import validate_price
        price=validate_price(json.loads(before['price_json']))
        if (before['status']!='unknown' or before['actual'] is not None or before['reserved']<=0
                or before['currency']!='CNY' or price['currency']!='CNY'
                or not before['authorization_id'] or before['model']!=record.get('model')
                or price['version']!=record.get('price_version')):
            raise ValueError('旧记录缺少可核验价格、原预留或累计授权；禁止转挂')
        receipt={'call_id':call_id,'operator':operator,'reason':reason,'before':before,
                 'after_status':HELD,'evidence_sha256':hashlib.sha256(raw).hexdigest(),
                 'basis':'historical_reservation_carried_not_zero_not_provider_invoice',
                 'created_at':datetime.now(timezone.utc).isoformat()}
        db.execute('CREATE TABLE IF NOT EXISTS transport_settlements (call_id TEXT PRIMARY KEY,receipt TEXT NOT NULL)')
        db.execute('INSERT INTO transport_settlements VALUES (?,?)',(call_id,json.dumps(receipt)))
        db.execute("UPDATE personal_calls SET status=? WHERE call_id=?",(HELD,call_id))
    return receipt
