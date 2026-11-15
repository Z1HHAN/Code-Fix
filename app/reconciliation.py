"""Operator-only settlement, with immutable evidence receipts. No network calls."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

def reconcile(ledger, *, call_id, currency, amount, evidence, operator, reason, no_inflight=False):
    if currency not in {'USD', 'CNY'} or no_inflight is not True:
        raise ValueError('须选择币种并确认该请求已结束，没有在途结算')
    try:
        value = Decimal(str(amount))
        if (not value.is_finite() or value < 0 or value > Decimal('9223372036854')
                or value * 1000000 != (value * 1000000).to_integral_value()):
            raise ValueError('金额须非负，最多6位小数')
    except InvalidOperation:
        raise ValueError('无效对账金额') from None
    if not operator.strip() or not reason.strip():
        raise ValueError('操作人和对账原因必填')
    proof = Path(evidence)
    if not proof.is_file() or proof.is_symlink() or proof.stat().st_size > 8*1024*1024:
        raise ValueError('对账凭据须为不超过8MiB的普通文件')
    digest = hashlib.sha256(proof.read_bytes()).hexdigest()
    # Identifiers cannot be SQL parameters. Select complete static statements;
    # all externally supplied values still use placeholders.
    select_sql, update_sql = {
        'CNY': ('SELECT * FROM cny_calls WHERE call_id=?',
                "UPDATE cny_calls SET status='known',actual_microcny=? WHERE call_id=?"),
        'USD': ('SELECT * FROM calls WHERE call_id=?',
                "UPDATE calls SET status='known',actual_microusd=? WHERE call_id=?"),
    }[currency]
    with sqlite3.connect(ledger.path, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS reconciliations (currency TEXT, call_id TEXT, receipt TEXT NOT NULL, PRIMARY KEY(currency,call_id))')
        cursor = db.execute(select_sql, (call_id,))
        row = cursor.fetchone()
        columns = [column[0] for column in cursor.description]
        personal = db.execute('SELECT * FROM personal_calls WHERE call_id=?', (call_id,))
        personal_row = personal.fetchone()
        if personal_row is not None:
            if row is not None:
                raise ValueError('请求编号跨账本冲突，需先核对')
            row, columns = personal_row, [column[0] for column in personal.description]
            if dict(zip(columns, row))['currency'] != currency:
                raise ValueError('对账币种与原始价格快照不一致')
            update_sql = "UPDATE personal_calls SET status='reconciled',actual=? WHERE call_id=?"
        if row is None:
            raise ValueError('请求不存在')
        before = dict(zip(columns,row))
        if before['status'] not in {'unknown','pending','reserved_unknown'}:
            raise ValueError('只允许对账未结请求；已结算记录不能覆盖')
        receipt = {'call_id':call_id,'currency':currency,'actual_amount':str(value),
                   'operator':operator,'reason':reason,'evidence_sha256':digest,
                   'confirmed_no_inflight':True,'before':before,
                   'created_at':datetime.now(timezone.utc).isoformat()}
        db.execute('INSERT INTO reconciliations VALUES (?,?,?)',(currency,call_id,json.dumps(receipt,ensure_ascii=False)))
        db.execute(update_sql, (int(value*1000000),call_id))
    return receipt
