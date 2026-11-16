"""Explicit follow-up evidence, separate from immutable paired experiments."""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

from .errors import InputValidationError


def register(comparisons, identifier, run_ids):
    original = comparisons.get(identifier)
    if not isinstance(run_ids, list) or not run_ids or len(run_ids) > 80 or not all(isinstance(r, str) for r in run_ids) or len(set(run_ids)) != len(run_ids):
        raise InputValidationError('补跑需显式且不重复的运行清单')
    pairs = {p['project']: p for p in original['pairs']}
    records = []
    for rid in run_ids:
        snap = comparisons.store.snapshot(rid)['run']
        project, arm = snap['task']['project'], snap['task'].get('strategy', 'dynamic')
        if project not in pairs or arm not in original.get('arms', ['fixed', 'dynamic']):
            raise InputValidationError('补跑题目或策略不属于原对照')
        base = pairs[project][arm]
        if not base['run_id'] or base['status'] == 'success':
            raise InputValidationError('只关联原有未成功题目的后续运行')
        row = comparisons._run(rid, project, arm, original['scope'])
        a, b = base['frozen']['identity'], row['frozen']['identity']
        if any(a.get(k) != b.get(k) for k in ['approved_files', 'test_suite_version']):
            raise InputValidationError('补跑必须使用相同的原始样本和冻结测试')
        if snap['created_at'] <= comparisons.store.snapshot(base['run_id'])['run']['created_at']:
            raise InputValidationError('补跑时间必须晚于原运行')
        records.append((identifier, rid, project, arm, json.dumps(row, ensure_ascii=False)))
    with comparisons.store.connect() as db:
        db.executemany('INSERT OR IGNORE INTO comparison_followups VALUES (?,?,?,?,?)', records)
    return latest(comparisons, identifier)


def costs(store, rows):
    known = sum((Decimal(r['cost']['amount_cny']) for r in rows if r['cost'].get('amount_cny') is not None), Decimal(0))
    unknown = sum(r['cost'].get('amount_cny') is None for r in rows)
    reserve = None
    path = store.root / 'model-ledger.sqlite3'
    if path.is_file() and not path.is_symlink():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_calls'").fetchone()
            if exists:
                ids = [r['run_id'] for r in rows]
                if ids:
                    calls = db.execute('SELECT run_id,currency,actual,reserved FROM personal_calls WHERE run_id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
                    # Personal CNY rows are authoritative for these run IDs. Do not
                    # erase other currencies or claim unknown requests cost zero.
                    covered = {r[0] for r in calls if r[1] == 'CNY'}
                    known = sum((Decimal(r['cost']['amount_cny']) for r in rows if r['run_id'] not in covered and r['cost'].get('amount_cny') is not None), Decimal(0))
                    known += Decimal(sum(r[2] or 0 for r in calls if r[1] == 'CNY')) / 1000000
                    reserve = Decimal(sum(r[3] for r in calls if r[1] == 'CNY' and r[2] is None)) / 1000000
                    unknown = sum(r[2] is None for r in calls if r[1] == 'CNY') + sum(r['run_id'] not in covered and r['cost'].get('amount_cny') is None for r in rows)
    return {'known_subtotal': str(known), 'unknown_requests_or_runs': unknown,
            'reserved_cny': str(reserve) if reserve is not None else None,
            'total': str(known) if not unknown else None}


def latest(comparisons, identifier):
    original = comparisons.get(identifier)
    with comparisons.store.connect() as db:
        records = [dict(r) for r in db.execute('SELECT * FROM comparison_followups WHERE comparison_id=? ORDER BY rowid', (identifier,))]
    view = deepcopy(original)
    arms = original.get('arms', ['fixed', 'dynamic'])
    followups = []
    by_key = {}
    for r in records:
        # Revalidate real tool evidence and identities rather than trusting a cached
        # success flag. No model calls, no changes to the original experiment.
        row = comparisons._run(r['run_id'], r['project'], r['arm'], original['scope'])
        followups.append({'project': r['project'], 'arm': r['arm'], **row})
        by_key.setdefault((r['project'], r['arm']), []).append(row)
    money = {}
    for arm in arms:
        all_rows = []
        for p in view['pairs']:
            base = p[arm]
            history = by_key.get((p['project'], arm), [])
            all_rows += ([base] if base['run_id'] else []) + history
            successful = [r for r in history if r['status'] == 'success']
            if history:
                chosen = successful[-1] if successful else history[-1]
                p[arm] = {**chosen, 'original_run_id': base['run_id'], 'original_status': base['status'],
                          'followup_count': len(history)}
        s = view['summary'][arm]
        s['success'] = sum(p[arm]['status'] == 'success' for p in view['pairs'])
        s['failed'] = sum(p[arm]['executed'] and p[arm]['status'] not in {'success', 'blocked'} for p in view['pairs'])
        s['blocked'] = sum(p[arm]['status'] == 'blocked' for p in view['pairs'])
        s['success_rate_all'] = s['success'] / s['planned'] if s['planned'] else None
        s['requests'] = sum(r.get('requests') or 0 for r in all_rows)
        s['candidate_rounds'] = sum(r.get('candidate_rounds') or 0 for r in all_rows)
        s['followup_runs'] = sum(r['arm'] == arm for r in followups)
        s['elapsed_seconds'] = sum(r['elapsed_seconds'] for r in all_rows) if all(r.get('elapsed_seconds') is not None for r in all_rows) else None
        money[arm] = costs(comparisons.store, all_rows)
        s['cost']['cny'].update(total=money[arm]['total'], known_subtotal=money[arm]['known_subtotal'])
    view['view'] = 'latest_cumulative'
    if original.get('kind') == 'agents':
        from .comparisons import paired_outcomes
        view['paired_outcomes'] = paired_outcomes(view['pairs'], arms)
    view['followups'] = followups
    view['cost_details'] = money
    view['basis_note'] = '每组独立运行相同题集；最新累计包含显式关联补跑。版本或资源上限可能变化，不作为同条件首试因果对照。调用、耗时与费用包含原运行及关联补跑；移交包为历史生成记录。'
    return view
