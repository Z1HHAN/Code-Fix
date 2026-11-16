"""Immutable, explicit paired-run evidence summaries. Never dispatches work."""
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import io
import json
import re

from .errors import InputValidationError
from .role_config import digest
from .run_store import now
from .tasks_v3 import get_task

STRATEGIES = ('fixed', 'dynamic')
KINDS = {'strategies': STRATEGIES, 'agents': ('single', 'dynamic')}


def paired_outcomes(rows, strategies):
    counts = dict(both_success=0, first_only=0, second_only=0, neither_success=0, incomplete=0)
    for row in rows:
        a, b = (row[s] for s in strategies)
        if not a['executed'] or not b['executed']:
            counts['incomplete'] += 1
            continue
        left, right = a['status'] == 'success', b['status'] == 'success'
        counts['both_success' if left and right else 'first_only' if left else 'second_only' if right else 'neither_success'] += 1
    return counts
# A-approved QuixBugs-Python scope. New registrations need explicit admission;
# collecting a plan does not qualify adapters or dispatch any of these tasks.
FORMAL_PROJECTS = frozenset({
    'qb-bitcount', 'qb-breadth_first_search', 'qb-bucketsort',
    'qb-depth_first_search', 'qb-detect_cycle', 'qb-find_first_in_sorted',
    'qb-find_in_sorted', 'qb-flatten', 'qb-gcd', 'qb-get_factors', 'qb-hanoi',
    'qb-is_valid_parenthesization', 'qb-kheapsort', 'qb-knapsack', 'qb-kth',
    'qb-lcs_length', 'qb-levenshtein', 'qb-lis', 'qb-longest_common_subsequence',
    'qb-max_sublist_sum', 'qb-mergesort', 'qb-minimum_spanning_tree',
    'qb-next_palindrome', 'qb-next_permutation', 'qb-pascal',
    'qb-possible_change', 'qb-powerset', 'qb-quicksort', 'qb-reverse_linked_list',
    'qb-rpn_eval', 'qb-shortest_path_length', 'qb-shortest_path_lengths',
    'qb-shortest_paths', 'qb-shunting_yard', 'qb-sieve', 'qb-sqrt',
    'qb-subsequences', 'qb-to_base', 'qb-topological_ordering', 'qb-wrap',
})


def amount(value):
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() and number >= 0 else None
    except InvalidOperation:
        return None


def summarize(rows, strategies=STRATEGIES):
    result = {}
    for strategy in strategies:
        group = [row[strategy] for row in rows]
        planned = len(group)
        executed = sum(r['executed'] for r in group)
        success = sum(r['status'] == 'success' for r in group)
        totals = {}
        for currency in ('cny', 'usd'):
            values = [r['cost'].get('amount_' + currency) for r in group
                      if r['run_id'] and currency.upper() in r['cost'].get('currencies', ['CNY', 'USD'])]
            known = [Decimal(v) for v in values if v is not None]
            totals[currency] = {'known_subtotal': str(sum(known, Decimal(0))),
                                'unknown_runs': sum(v is None for v in values),
                                'total': str(sum(known, Decimal(0))) if values and all(v is not None for v in values) else None}
        result[strategy] = {'planned': planned, 'ran': sum(bool(r['run_id']) for r in group),
                            'executed': executed, 'success': success,
                            'failed': sum(r['executed'] and r['status'] not in {'success', 'blocked'} for r in group),
                            'blocked': sum(r['status'] == 'blocked' for r in group),
                            'not_run': sum(not r['run_id'] for r in group),
                            'success_rate_all': success / planned if planned else None,
                            'success_rate_executed': success / executed if executed else None,
                            'candidate_rounds': sum(r['candidate_rounds'] or 0 for r in group),
                            'requests': sum(r['requests'] or 0 for r in group), 'cost': totals}
    return result


class ComparisonStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS comparisons (comparison_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def _run(self, rid, project, strategy, scope):
        run = self.store.snapshot(rid)
        record, events = run['run'], run['events']
        if record['status'] in {'created', 'running'}:
            raise InputValidationError('对照仅引用已结束运行；不会启动或等待任务')
        task, result = record['task'], record['result'] or {}
        if task['mode'] != 'repair' or task['project'] != project or task.get('strategy', 'dynamic') != strategy:
            raise InputValidationError('配对题目、模式或策略不匹配')
        path = self.store.directory(rid) / 'trace/experiment.json'
        if not path.is_file() or path.is_symlink():
            raise InputValidationError('历史运行缺少冻结对照身份；不能用当前配置补造')
        frozen = json.loads(path.read_text(encoding='utf-8'))
        identity = frozen['identity']
        if (not isinstance(identity, dict) or not isinstance(identity.get('bindings'), dict)
                or any(not isinstance(b, dict) for b in identity['bindings'].values())
                or not isinstance(identity.get('code'), dict)):
            raise InputValidationError('冻结条件不一致或身份格式无效')
        if digest(identity) != frozen['identity_sha256'] or frozen['strategy'] != strategy:
            raise InputValidationError('冻结身份校验失败')
        if scope != 'offline' and (frozen['execution_kind'] != 'real' or (identity['code']['dirty'] is not False and identity['code'].get('verified_bundle') is not True)
                                   or not identity['code']['commit']):
            raise InputValidationError('真实对照须使用干净冻结提交和真实执行器；离线夹具须单列')
        if scope != 'offline' and any(not b.get('profile_ref') for b in identity['bindings'].values()):
            raise InputValidationError('旧live_env缺少可核验档案身份，须保存批准模型配置后创建新任务')
        if scope == 'formal' and result.get('injected'):
            raise InputValidationError('注入运行不得进入正式实验')
        candidates = sum(e['event'] == 'candidate_applied' for e in events)
        requests = sum(e['event'] == 'agent_call' and e.get('model_api_called') is True for e in events)
        # A run counts as executed only once the registered baseline produced tool evidence.
        executed = any(e['event'] == 'test_completed' for e in events)
        cost = result.get('cost') or {}
        if (self.store.root / 'model-ledger.sqlite3').exists():
            from .live_model import Ledger
            cost = Ledger(self.store.root).summary(rid)
        currencies = sorted({b['currency'] for b in identity['bindings'].values() if b.get('currency')})
        money = {f'amount_{c}': amount(cost.get(f'amount_{c}')) for c in ('cny', 'usd')}
        if cost.get('status') == 'not_called' and not requests:
            money = {f'amount_{c}': '0' if c.upper() in currencies else None for c in ('cny', 'usd')}
        if cost.get('status') in {'unknown', 'pending', 'not_applicable'}:
            money = {'amount_cny': None, 'amount_usd': None}
        elapsed = None
        starts = [e['created_at'] for e in events if e['event'] == 'started']
        ends = [e['created_at'] for e in events if e['event'] == 'finished']
        if starts and ends:
            elapsed = max(0, (datetime.fromisoformat(ends[-1]) - datetime.fromisoformat(starts[0])).total_seconds())
        passed = record['status'] == 'success' and result.get('final_result') == 'passed'
        if record['status'] == 'success' and not passed:
            raise InputValidationError('成功记录缺少最终复验通过证据')
        if passed and not any(e['event'] == 'test_completed' and e.get('phase') == 'final_check'
                              and e.get('verification_status') == 'passed' and e.get('failure_class') == 'passed'
                              and e.get('code_version') == result.get('code_version') for e in events):
            raise InputValidationError('成功记录缺少同版本干净final工具证据')
        return {'run_id': rid, 'status': record['status'], 'executed': executed,
                'candidate_rounds': candidates, 'role_calls': result.get('model_calls'), 'requests': requests,
                'cost': {**money, 'currencies': currencies, 'status': cost.get('status', 'unknown')}, 'elapsed_seconds': elapsed,
                'reason': result.get('reason'), 'handling_status': result.get('handling_status'),
                'identity_sha256': frozen['identity_sha256'], 'frozen': frozen,
                'report_url': f'/api/v3/tasks/{rid}/report', 'bundle_url': f'/api/v3/tasks/{rid}/bundle',
                'events_url': f'/api/v3/tasks/{rid}/events'}

    def create(self, value):
        if not isinstance(value, dict) or not {'title', 'scope', 'pairs'} <= set(value) or set(value) - {'title', 'scope', 'pairs', 'kind'}:
            raise InputValidationError('需要title、scope和显式pairs清单')
        kind = value.get('kind', 'strategies')
        if not isinstance(kind, str) or kind not in KINDS:
            raise InputValidationError('kind须为strategies/agents')
        strategies = KINDS[kind]
        if not isinstance(value['title'], str) or not 1 <= len(value['title'].strip()) <= 120:
            raise InputValidationError('对照标题须为1至120字符')
        if not isinstance(value['scope'], str) or value['scope'] not in {'formal', 'controlled', 'offline'}:
            raise InputValidationError('scope须为formal/controlled/offline')
        if not isinstance(value['pairs'], list) or not 1 <= len(value['pairs']) <= 40:
            raise InputValidationError('pairs须为1至40项计划清单')
        rows, seen_projects, seen_runs = [], set(), set()
        for pair in value['pairs']:
            if not isinstance(pair, dict) or set(pair) != {'project', 'not_run_reason', *strategies}:
                raise InputValidationError('每项需project、两组运行及not_run_reason')
            project = get_task(pair['project']).project
            if project in seen_projects:
                raise InputValidationError('同一清单题目不能重复；不接受挑选最佳重跑')
            if value['scope'] == 'formal' and project not in FORMAL_PROJECTS:
                raise InputValidationError('正式计划仅包含已批准的40道QuixBugs-Python题目')
            seen_projects.add(project)
            reasons = pair['not_run_reason']
            if not isinstance(reasons, dict) or set(reasons) - set(strategies):
                raise InputValidationError('未运行原因只能引用本对照的两组')
            row = {'project': project}
            for strategy in strategies:
                rid = pair[strategy]
                if rid is None:
                    reason = reasons.get(strategy)
                    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
                        raise InputValidationError('未运行项必须说明原因')
                    row[strategy] = {'run_id': None, 'status': 'not_run', 'executed': False,
                                     'candidate_rounds': None, 'requests': None, 'elapsed_seconds': None,
                                     'cost': {'amount_cny': None, 'amount_usd': None, 'status': 'not_run'}, 'reason': reason}
                else:
                    if not isinstance(rid, str) or rid in seen_runs:
                        raise InputValidationError('运行编号必须为不重复字符串')
                    seen_runs.add(rid)
                    row[strategy] = self._run(rid, project, strategy, value['scope'])
            if all(row[s]['run_id'] for s in strategies):
                if row[strategies[0]]['identity_sha256'] != row[strategies[1]]['identity_sha256']:
                    raise InputValidationError('两组冻结条件不一致：须核对提交、模型、指令、参数、价格、测试及上限')
            row['paired'] = all(row[s]['run_id'] for s in strategies)
            rows.append(row)
        data = {'schema_version': 1, 'title': value['title'], 'scope': value['scope'],
                'plan': value['pairs'], 'pairs': rows, 'summary': summarize(rows, strategies),
                'basis': 'explicit_manifest; immutable_evidence_snapshot; no_task_dispatch',
                'review_status': 'pending_B_C_review'}
        if 'kind' in value:
            data.update(kind=kind, arms=list(strategies))
        if kind == 'agents':
            data['paired_outcomes'] = paired_outcomes(rows, strategies)
        identifier = 'cmp-' + digest(data)
        data.update(comparison_id=identifier, created_at=now())
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?)', (identifier, json.dumps(data, ensure_ascii=False)))
        return self.get(identifier)

    def get(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'cmp-[a-f0-9]{64}', identifier):
            raise InputValidationError('非法对照编号')
        with self.store.connect() as db:
            row = db.execute('SELECT payload FROM comparisons WHERE comparison_id=?', (identifier,)).fetchone()
        if row is None:
            raise InputValidationError('对照不存在')
        data = json.loads(row[0])
        if 'cmp-' + digest({k: v for k, v in data.items() if k not in {'comparison_id', 'created_at'}}) != identifier:
            raise InputValidationError('对照内容校验失败')
        return data

    def list(self):
        with self.store.connect() as db:
            return [dict(comparison_id=d['comparison_id'], title=d['title'], scope=d['scope'], created_at=d['created_at'],
                         kind=d.get('kind','strategies'),
                         planned=sum(s.get('planned',len(d['pairs'])) for s in d['summary'].values()),
                         ran=sum(s.get('ran',sum(bool(p[a]['run_id']) for p in d['pairs'])) for a,s in d['summary'].items()))
                    for row in db.execute('SELECT payload FROM comparisons ORDER BY rowid DESC LIMIT 100')
                    for d in [json.loads(row[0])]]

    def metrics(self, identifier):
        """Read-only presentation metrics; never alter or re-sign a snapshot."""
        data = self.get(identifier)
        groups = {}
        for arm in data.get('arms', STRATEGIES):
            rows = [p[arm] for p in data['pairs'] if p[arm]['run_id']]
            handoffs, accepted, missing = 0, 0, 0
            for row in rows:
                path = self.store.directory(row['run_id']) / 'handoff.json'
                if not path.is_symlink() and path.is_file() and path.stat().st_size <= 262144:
                    try:
                        proof = json.loads(path.read_text(encoding='utf-8'))
                        if proof.get('run_id') != row['run_id'] or proof.get('handling_status') != 'pending_human':
                            raise ValueError('移交证据不匹配')
                        handoffs += 1
                        accepted += int(proof.get('accepted_by') is not None)
                        continue
                    except (ValueError, OSError):
                        pass
                if row['status'] != 'success':
                    missing += 1
            groups[arm] = {'handoff_packages': handoffs if not missing else None,
                           'handoff_rate': handoffs / len(rows) if rows and not missing else None,
                           'handoff_missing_evidence': missing,
                           'human_acceptance_records': accepted,
                           'denominator': len(rows)}
        return {'comparison_id': identifier, 'groups': groups,
                'definition': '人工移交包生成比例，不表示人工实际介入或处理完成；均值按已运行任务计算'}


def export_csv(data):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['project', 'strategy', 'run_id', 'status', 'executed', 'candidate_rounds',
                     'requests', 'amount_cny', 'amount_usd', 'cost_status', 'elapsed_seconds', 'reason', 'report_url'])
    for pair in data['pairs']:
        for strategy in data.get('arms', STRATEGIES):
            r = pair[strategy]
            values = [pair['project'], strategy, r['run_id'], r['status'], r['executed'], r['candidate_rounds'],
                      r['requests'], r['cost']['amount_cny'], r['cost']['amount_usd'], r['cost']['status'],
                      r['elapsed_seconds'], r['reason'], r.get('report_url')]
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values])
    return '\ufeff' + stream.getvalue()
