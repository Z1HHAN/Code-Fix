"""Evidence-driven repair state machine; final-check feedback never enters agents."""
import json
import re
from pathlib import Path
import time

from .agents_v3 import AgentGateway
from .candidates import apply_candidate
from .config import read_json
from .decision import DecisionEngine
from .docker_runner import IsolationBlocked
from .split_runner import SplitDockerRunner
from .live_model import Ledger, ModelBlocked
from .model_profiles import ModelRouter
from .runner import code_version
from .tasks_v3 import get_task
from .role_config import RoleConfig
from .config import ConfigError
from .trace import RunTrace
from .diagnostics import record_exception

ROOT = Path(__file__).resolve().parents[1]
# Production guards precede the historical v0.2 rule table. Invalid output does
# not enter that table's R15 repair retry; this release deliberately stops once.
RUNTIME_POLICY = {'version': '1.0.0', 'invalid_model_output': 'stop',
                  'transport_attempts': 1, 'final_feedback_to_agents': False}
# A baseline run reproduces the registered defect when the frozen native tests
# fail through an assertion or through a candidate runtime error.  Both classes
# were approved by the operator on 2026-11-06 (option A); syntax_error,
# environment_error and timeout still stop before any repair round.
BASELINE_FAILURE_CLASSES = frozenset({'assertion_failed', 'code_runtime_error'})


class Workflow:
    def __init__(self, store, runner=None, model=None):
        self.store = store
        self.runner = runner
        self.model = model
        self.model_calls = 0
        self.tool_calls = 0

    def run(self, run_id):
        context = WorkflowRun(self, run_id)
        try:
            return context.run()
        finally:
            self.runner, self.model = context.runner, context.model
            self.model_calls, self.tool_calls = context.model_calls, context.tool_calls


class WorkflowRun:
    """State for one run; step methods share explicit, inspectable attributes."""

    def __init__(self, workflow, run_id):
        self.store, self.runner, self.model = workflow.store, workflow.runner, workflow.model
        self.model_calls = self.tool_calls = 0
        self.run_id = run_id
        self.engine = None
        self.task = self.store.get(self.run_id)['task']
        self.definition = get_task(self.task['project'])
        self.source = self.definition.source
        self.runner = self.runner or SplitDockerRunner(self.task['project'])
        self.runner.cancelled = self.cancelled
        self.directory = self.store.directory(self.run_id)
        self.trace = RunTrace(self.store, self.run_id)
        self.latest_evidence = []
        self.deadline = time.monotonic() + self.task['total_seconds']
        self.final = {'final_result': 'not_run', 'dev_result': 'not_run', 'rounds_used': 0,
                 'model_calls': 0, 'tool_calls': 0, 'model_api_called': False,
                 'mode': getattr(self.model, 'mode', 'live'), 'isolation_ready': False,
                 'project': self.task['project'], 'test_suite_version': self.definition.summary()['test_suite_version']}

    def cancelled(self):
        stored = self.store.get(self.run_id)
        return stored['control'] == 'stop' or stored['status'] not in {'created', 'running'}

    def counters(self):
        ledger = getattr(self.model, 'ledger', None)
        cost = ledger.summary(self.run_id) if ledger else {
            'status': 'not_applicable' if self.model_calls else 'not_called', 'amount_usd': None}
        return {'model_calls': self.model_calls, 'tool_calls': self.tool_calls,
                'rounds_used': self.final['rounds_used'], 'cost': cost,
                'remaining_seconds': max(0, self.deadline - time.monotonic()),
                'limits': {k: self.task[k] for k in ('max_rounds', 'max_calls', 'total_seconds')}}

    def finish(self, status, reason):
        self.final.update(reason=reason, model_calls=self.model_calls, tool_calls=self.tool_calls,
                     cost=self.counters()['cost'])
        self.trace.complete('failed' if status == 'failed' else 'interrupted', reason=reason)
        action = 'finish' if status in {'success', 'failed'} else 'pause' if status == 'paused' else 'stop'
        self.trace.decision(program={'action': action,
                                'rule_id': self.final.get('rule_id', self.final.get('guard_id', 'terminal_' + status)), 'reason': reason},
                       effective_action=action,
                       reason=reason, evidence_ids=self.latest_evidence, version=self.final.get('code_version'),
                       suite_version=self.final['test_suite_version'],
                       counters=self.counters(), round_number=self.final['rounds_used'],
                       phase='final_check' if self.latest_evidence == ['test-final-check'] else 'development')
        self.final['trace_version'] = '1.0.0'
        if status != 'success':
            self.final['handoff_path'] = self.trace.handoff(status=status, result=self.final, task=self.task)
            self.final['handling_status'] = 'pending_human'
        return status, self.final

    def boundary(self):
        stored = self.store.get(self.run_id)
        if stored['status'] not in {'created', 'running'}:
            raise ControlRequested('stop')
        control = stored['control']
        if control:
            raise ControlRequested(control)
        if time.monotonic() >= self.deadline:
            raise ModelBlocked('任务总时限到达')

    def route(self, failure, phase='development', pause=False):
        context = dict(phase=phase, failure_class=failure, isolation_ready=True,
                       user_pause_requested=pause, evidence_matches_current_version=True,
                       repair_budget_available=self.final['rounds_used'] < self.task['max_rounds'],
                       execution_budget_available=time.monotonic() < self.deadline,
                       needs_new_repair=failure != 'passed', next_action_requires_execution_budget=True,
                       output_agent=None, retries_left=self.task['max_rounds']-self.final['rounds_used'])
        import uuid
        decision = self.engine.decide(context, run_id=self.run_id, code_version=self.final.get('code_version'),
                                 evidence_ids=self.latest_evidence, decision_id='d-'+uuid.uuid4().hex)
        self.final['rule_id'] = decision['rule_id']
        self.trace.decision(program=decision, effective_action=decision['action'], reason=decision['reason'],
                       evidence_ids=self.latest_evidence, version=self.final.get('code_version'),
                       suite_version=self.final['test_suite_version'], counters=self.counters(),
                       round_number=self.final['rounds_used'], phase=phase)
        return decision

    def test(self, work, phase, name):
        self.boundary()
        step = self.trace.start('pytest', round_number=self.final['rounds_used'], phase=phase,
                           evidence_ids=self.latest_evidence)
        self.tool_calls += 1
        folder = self.directory / name
        result = self.runner.run(work, folder, phase=phase,
                                 timeout=max(.01, min(60, self.deadline-time.monotonic())),
                                 expected_version=code_version(work, (self.source,)))
        eid = 'test-' + name
        self.latest_evidence[:] = [eid]
        self.trace.evidence_ids.append(eid)
        self.store.event(self.run_id, {'event': 'test_completed', **step, 'evidence_id': eid,
                                 'evidence_path': name, **result})
        if (result.get('phase') != phase or result.get('code_version') != code_version(work, (self.source,))
                or (result.get('test_suite_version') is not None
                    and result['test_suite_version'] != self.final['test_suite_version'])):
            raise IsolationBlocked('执行后证据版本不一致')
        self.trace.complete(verification_status=result['verification_status'], evidence_id=eid,
                       failure_class=result['failure_class'],
                       evidence_path=name, code_version=result['code_version'],
                       test_suite_version=result.get('test_suite_version'),
                       registered_test_suite_version=self.final['test_suite_version'])
        return result, folder


    @staticmethod
    def excerpt(result, folder, name):
        path = folder / result.get(name, 'missing')
        if not path.is_file():
            return ''
        with path.open('rb') as stream:
            text = stream.read(6000).decode('utf-8', errors='replace')
        # Native parameterized nodeids embed expected values; never forward
        # those values or assertion diffs as model context.
        return '\n'.join(re.sub(r'\[[^\]\n]*\]', '[parameters redacted]', line)
                         for line in text.splitlines()
                         if not line.startswith(('CODEFIX_REPORT:', 'E ', '> ', 'assert ')))

    @staticmethod
    def diagnostics(result, folder):
        """Extract allowlisted types/module identifiers, never arbitrary messages."""
        allowed = {'ImportError', 'ModuleNotFoundError', 'SyntaxError', 'IndentationError',
                   'TabError', 'TypeError', 'ValueError', 'AttributeError', 'NameError',
                   'UnboundLocalError', 'RuntimeError', 'OSError', 'FileNotFoundError',
                   'PermissionError', 'TimeoutError', 'AssertionError', 'KeyError',
                   'IndexError', 'ZeroDivisionError', 'RecursionError', 'MemoryError'}
        exception_types = []
        missing_modules = []
        for name in ('stdout_path', 'stderr_path'):
            path = folder / result.get(name, 'missing')
            if not path.is_file():
                continue
            with path.open('rb') as stream:
                text = stream.read(8000).decode('utf-8', errors='replace')
            for line in text.splitlines():
                # Accept pytest's E prefix and explicit ErrorType headers;
                # assert lines, source lines and traceback frames never match.
                match = re.match(r'^\s*(?:E\s+)?(?:ErrorType:\s*)?([A-Za-z][A-Za-z0-9]*)(?::|\s*$)', line)
                if not match or match.group(1) not in allowed:
                    continue
                exc_type = match.group(1)
                if exc_type not in exception_types:
                    exception_types.append(exc_type)
                if exc_type not in {'ImportError', 'ModuleNotFoundError'}:
                    continue
                module = re.fullmatch(
                    r'''\s*No module named ['"]([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)['"]\s*''',
                    line[match.end():])
                if module and len(module.group(1)) <= 200 and module.group(1) not in missing_modules:
                    missing_modules.append(module.group(1))
        return {'exception_types': exception_types, 'missing_modules': missing_modules} if exception_types else None

    @classmethod
    def evidence(cls, result, folder):
        # No full history, secrets, reference implementation or final evidence.
        summary = {'counts': result.get('counts'), 'exit_code': result.get('exit_code'),
                   'failure_class': result['failure_class'], 'stdout': cls.excerpt(result, folder, 'stdout_path'),
                   'stderr': cls.excerpt(result, folder, 'stderr_path'),
                   'diagnostics': cls.diagnostics(result, folder)}
        return {'evidence_id': 'test-' + folder.name, 'phase': 'development', 'code_version': result['code_version'],
                'failure_class': result['failure_class'], 'summary': json.dumps(summary, ensure_ascii=False)}

    def call(self, role, ev, round_number, extra=None):
        self.boundary()
        if self.model_calls >= self.task['max_calls']:
            raise ModelBlocked('模型调用次数到达上限')
        step = self.trace.start(role, round_number=round_number, evidence_ids=[ev['evidence_id']])
        self.model_calls += 1
        record = self.gateway.invoke(role, run_id=self.run_id, round_number=round_number,
                                code=(self.current / self.source).read_text(encoding='utf-8'), evidence=ev,
                                version=self.version, work=self.current, task=self.task, deadline=self.deadline, extra=extra,
                                trace={k: step[k] for k in ('step_id', 'parent_step_id')})
        self.final['model_api_called'] |= record.get('model_api_called', False)
        budget = record.get('cost', {}).get('budget')
        if budget and budget.get('alerts'):
            self.store.event(self.run_id, {'event': 'budget_alert', 'step_id': step['step_id'],
                                     'budget': budget, 'reason': '人民币账号额度提醒；非平台实时余额'})
        self.trace.complete(record['status'], call_id=record['call_id'],
                       artifact_path='calls/' + record['call_id'],
                       code_version=self.version, failure_class=record.get('failure_class'))
        # A stop/pause received while the model was in flight must be
        # observed before applying its output or starting another step.
        self.boundary()
        if record['status'] != 'ok':
            self.final['failure_class'] = record.get('failure_class', 'unknown')
            if self.final['failure_class'] == 'invalid_model_output':
                self.final['guard_id'] = 'V3_INVALID_MODEL_OUTPUT_STOP'
            elif self.final['failure_class'] in {'permission_violation', 'model_transport_error'}:
                self.final['guard_id'] = 'V3_' + self.final['failure_class'].upper()
                routed = self.route(self.final['failure_class'])
                raise RoutedFailure('paused' if routed['action'] == 'pause' else 'stopped', record.get('reason', routed['reason']))
            raise ModelBlocked('角色调用失败：' + record.get('failure_class', 'unknown')
                               + ('；' + record['reason'] if record.get('reason') else ''))
        return record['parsed_output']

    def prepare(self):
        self.boundary()
        if self.definition.admission_blocker:
            return self.finish('blocked', self.definition.admission_blocker)
        config = RoleConfig(ROOT)
        config.preflight()
        resolved = None
        blockers = []
        if self.model is None:
            try:
                resolved = ModelRouter.resolve(config)
            except ModelBlocked as exc:
                blockers.append(str(exc))
        self.trace.start('isolation_preflight', phase='preflight')
        isolation = self.runner.preflight(self.directory / 'isolation')
        self.store.event(self.run_id, {'event': 'isolation_checked', **isolation})
        self.trace.complete('ok' if isolation['isolation_ready'] else 'blocked', evidence_path='isolation', **isolation)
        self.final['isolation_ready'] = isolation['isolation_ready']
        self.boundary()
        if not isolation['isolation_ready']:
            blockers.append(isolation.get('reason', '隔离未就绪'))
        if blockers:
            self.final['blockers'] = blockers
            return self.finish('blocked', '运行前置条件未满足；未发起模型请求或候选执行')
        if self.model is None:
            self.model = ModelRouter(config, Ledger(self.store.root), resolved)
        self.final['mode'] = getattr(self.model, 'mode', 'unknown')
        rules = read_json(ROOT / 'configs/decision_rules.json')
        self.trace.write('trace/configuration.json', {'roles': config.snapshot, 'config_sha256': config.sha256,
                    'runtime_limits': read_json(ROOT / 'configs/runtime_limits.json'),
                    'bindings': {role: self.model.binding(role) for role in config.agents}
                    if hasattr(self.model, 'binding') else {'mode': getattr(self.model, 'mode', 'unknown')},
                    'rules': rules,
                    'production_guards': RUNTIME_POLICY,
                    'model_profiles': getattr(self.model, 'registry', None),
                    'provenance': {role: config.provenance(role) for role in config.agents}})
        self.gateway = AgentGateway(self.model, self.store, config)
        self.engine = DecisionEngine(rules)
        self.base = self.definition.prepare(self.directory / 'base')
        self.current = self.base
        self.version = code_version(self.current, (self.source,))
        self.final.update(code_version=self.version, code_path='base/' + self.source)
        self.location = None

    def final_check(self):
        # Replay full final source on original clean input, never reuse development workspace.
        final_work = self.directory / 'final-work'
        apply_candidate(self.base, final_work, [{'path': self.source, 'new_content': (self.current / self.source).read_text(encoding='utf-8')}],
                        self.directory / 'final-patch', project=self.task['project'])
        checked, _ = self.test(final_work, 'final_check', 'final-check')
        self.final['final_result'] = checked['verification_status']
        self.boundary()
        final_failure = checked['failure_class']
        if final_failure in {'assertion_failed', 'syntax_error', 'code_runtime_error'}:
            final_failure = 'final_check_failed'
        routed = self.route(final_failure, phase='final_check')
        if checked['verification_status'] == 'passed' and checked['failure_class'] == 'passed':
            return self.finish('success', '开发测试及干净副本同套原生测试均实际通过')
        return self.finish('failed' if routed['action'] == 'finish' else 'paused', '独立验收未通过；结果不回流本轮Agent')

    def repair_round(self, result, folder, round_number):
        ev = self.evidence(result, folder)
        self.final['rounds_used'] = round_number
        candidate = self.call('repairer', ev, round_number, self.location)
        target = self.directory / f'candidate-{round_number}'
        self.trace.start('apply_candidate', round_number=round_number, evidence_ids=[ev['evidence_id']])
        patch = apply_candidate(self.current, target, candidate['changes'], self.directory / f'patch-{round_number}', project=self.task['project'])
        self.current, self.version = target, patch['candidate_version']
        self.final.update(code_version=self.version, code_path=f'candidate-{round_number}/' + self.source)
        self.store.event(self.run_id, {'event': 'candidate_applied', 'round': round_number, **patch})
        self.trace.complete(code_version=self.version, artifact_path=f'patch-{round_number}')
        result, folder = self.test(self.current, 'development', f'development-{round_number}')
        self.final.update(dev_result=result['verification_status'], code_version=self.version)
        self.boundary()
        ev = self.evidence(result, folder)
        # Terminal safety branches take precedence over all model advice.
        context = dict(phase='development', failure_class=result['failure_class'], isolation_ready=True,
                       user_pause_requested=False, evidence_matches_current_version=True,
                       repair_budget_available=round_number < self.task['max_rounds'],
                       execution_budget_available=time.monotonic() < self.deadline,
                       needs_new_repair=result['failure_class'] != 'passed',
                       next_action_requires_execution_budget=True, output_agent=None,
                       retries_left=self.task['max_rounds']-round_number)
        decision = self.engine.decide(context, run_id=self.run_id, code_version=self.version,
                                 evidence_ids=[ev['evidence_id']], decision_id=f'd-{round_number}')
        if decision['action'] in {'pause', 'stop'}:
            self.trace.decision(program=decision, effective_action=decision['action'], reason=decision['reason'],
                           evidence_ids=[ev['evidence_id']], version=self.version,
                           suite_version=self.final['test_suite_version'], counters=self.counters(), round_number=round_number)
            return result, folder, self.finish('paused' if decision['action'] == 'pause' else 'stopped', decision['reason'])
        # Advisory model calls must not consume the last chance to
        # validate an already passing candidate with the tool executor.
        if decision['action'] == 'final_check' and self.task['max_calls']-self.model_calls < 2:
            advice = {'action': 'final_check', 'reason': '开发通过；预算不足时跳过非必需模型建议'}
        else:
            detector = self.call('detector', ev, round_number)
            advice = self.call('scheduler', ev, round_number, detector)
        effective = advice['action'] if advice['action'] in {'pause', 'stop'} else decision['action']
        reason = advice['reason'] if effective in {'pause', 'stop'} else decision['reason']
        override = ('模型要求保守停止，程序允许停止；不采纳推进动作'
                    if effective in {'pause', 'stop'} else '模型建议不能覆盖工具证据与强制规则') if advice['action'] != decision['action'] else None
        if effective == 'locate' and self.model_calls >= self.task['max_calls']:
            effective, reason = 'stop', '模型调用次数到达上限'
            override = '定位需要新增模型调用；剩余调用预算不足，停止调度'
            self.final['guard_id'] = 'V3_MODEL_CALL_LIMIT'
        self.trace.decision(program=decision, effective_action=effective, reason=reason,
                       advice=advice, override_reason=override,
                       evidence_ids=[ev['evidence_id']], version=self.version,
                       suite_version=self.final['test_suite_version'], counters=self.counters(), round_number=round_number)
        if self.final.get('guard_id') == 'V3_MODEL_CALL_LIMIT':
            return result, folder, self.finish('blocked', reason)
        if advice['action'] in {'pause', 'stop'}:
            return result, folder, self.finish('paused' if advice['action'] == 'pause' else 'stopped', advice['reason'])
        if decision['action'] == 'final_check':
            return result, folder, self.final_check()
        # A model cannot force final validation of failing code or bypass syntax rules.
        if effective == 'locate':
            self.location = self.call('locator', ev, round_number)
        else:
            self.location = None
        return result, folder, None

    def run(self):
        try:
            terminal = self.prepare()
            if terminal is not None:
                return terminal
            result, folder = self.test(self.base, 'development', 'baseline')
            self.boundary()
            baseline_failure = result.get('failure_class', 'unknown_evidence')
            self.final['baseline_failure_class'] = baseline_failure
            if baseline_failure not in BASELINE_FAILURE_CLASSES:
                routed = self.route('not_reproduced' if baseline_failure == 'passed' else baseline_failure)
                return self.finish('stopped' if routed['action'] == 'stop' else 'paused', routed['reason'])
            for round_number in range(1, self.task['max_rounds'] + 1):
                result, folder, terminal = self.repair_round(result, folder, round_number)
                if terminal is not None:
                    return terminal
            return self.finish('stopped', '达到最大修复轮次')
        except ControlRequested as exc:
            if exc.action == 'pause' and self.engine is not None:
                self.route('user_pause', pause=True)
            else:
                self.final['guard_id'] = 'V3_OPERATOR_STOP'
            self.trace.complete('interrupted', reason='用户控制')
            return self.finish('paused' if exc.action == 'pause' else 'stopped', '按用户请求在步骤边界停止推进')
        except RoutedFailure as exc:
            return self.finish(exc.status, str(exc))
        except (ModelBlocked, IsolationBlocked, ConfigError) as exc:
            return self.finish('blocked', str(exc))
        except Exception as exc:
            self.final.update(record_exception(self.directory, exc, model=self.model))
            return self.finish('failed', '输出、补丁或执行证据无效，已停止')


class ControlRequested(Exception):
    def __init__(self, action):
        self.action = action


class RoutedFailure(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
