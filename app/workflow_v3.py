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
DIAGNOSTIC_TYPES = frozenset({
    'ImportError', 'ModuleNotFoundError', 'SyntaxError', 'IndentationError',
    'TabError', 'TypeError', 'ValueError', 'AttributeError', 'NameError',
    'UnboundLocalError', 'RuntimeError', 'OSError', 'FileNotFoundError',
    'PermissionError', 'TimeoutError', 'AssertionError', 'KeyError',
    'IndexError', 'ZeroDivisionError', 'RecursionError', 'MemoryError',
})


def _exception_header(line):
    """Recognize trusted type names, including pytest short summaries."""
    if line.lstrip().startswith(('FAILED ', 'ERROR ')) and ' - ' in line:
        line = line.split(' - ', 1)[1]
    match = re.match(r'^\s*(?:E\s+)?(?:ErrorType:\s*)?([A-Za-z][A-Za-z0-9]*)(?::|\s*$)', line)
    return match if match and match.group(1) in DIAGNOSTIC_TYPES else None


# Repairer format failures consume a round; advisory format correction is bounded.
RUNTIME_POLICY = {'version': '1.2.0', 'invalid_model_output': 'bounded_retry',
                  'advisory_format_retries': 1,
                  'candidate_detector_required': True, 'locator_requires_detector_and_dispatch': True,
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
            from .remote_runner import RemoteRunner
            if isinstance(context.runner, RemoteRunner):
                context.runner.close()


class WorkflowRun:
    """State for one run; step methods share explicit, inspectable attributes."""

    def __init__(self, workflow, run_id):
        self.store, self.runner, self.model = workflow.store, workflow.runner, workflow.model
        self.model_calls = self.tool_calls = 0
        self.actual_requests = 0
        self.output_feedback = None
        self.agent_outputs = {}
        self.pending_loop = None
        self.empty_reasoning_streak = {}
        self.run_id = run_id
        self.engine = None
        self.task = self.store.get(self.run_id)['task']
        self.strategy = self.task.get('strategy', 'dynamic')
        self.definition = get_task(self.task['project'])
        self.source = self.definition.source
        if self.runner is None:
            from .remote_runner import execution_runner
            self.runner = execution_runner(self.task['project'], local_factory=SplitDockerRunner)
        self.runner.cancelled = self.cancelled
        self.directory = self.store.directory(self.run_id)
        self.trace = RunTrace(self.store, self.run_id)
        self.latest_evidence = []
        self.deadline = time.monotonic() + self.task['total_seconds']
        self.runner.deadline = self.deadline
        self.final = {'final_result': 'not_run', 'dev_result': 'not_run', 'rounds_used': 0,
                 'model_calls': 0, 'tool_calls': 0, 'model_api_called': False,
                 'mode': getattr(self.model, 'mode', 'live'), 'isolation_ready': False,
                 'strategy': self.strategy,
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
                     model_attempts=self.model_calls, actual_model_requests=self.actual_requests,
                     cost=self.counters()['cost'])
        self.trace.complete({'success': 'ok', 'failed': 'failed', 'blocked': 'blocked'}.get(status, 'interrupted'), reason=reason)
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
        context = dict(phase=phase, failure_class=failure, isolation_ready=self.final['isolation_ready'],
                       user_pause_requested=pause, evidence_matches_current_version=self.final.get('evidence_matches_current_version', False),
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
        expected_version = self.version if phase == 'final_check' else code_version(work, (self.source,))
        if phase == 'final_check' and code_version(work, (self.source,)) != expected_version:
            raise IsolationBlocked('最终候选版本已改变')
        from .runtime_limits import runtime_limits
        limit = runtime_limits()['max_tool_calls']
        if self.tool_calls >= limit:
            raise ModelBlocked('工具调用次数到达工程上限')
        step = self.trace.start('pytest', round_number=self.final['rounds_used'], phase=phase,
                           evidence_ids=self.latest_evidence)
        self.tool_calls += 1
        folder = self.directory / name
        result = self.runner.run(work, folder, phase=phase,
                                 timeout=max(.01, min(60, self.deadline-time.monotonic())),
                                 expected_version=expected_version)
        eid = 'test-' + name
        self.latest_evidence[:] = [eid]
        self.trace.evidence_ids.append(eid)
        self.store.event(self.run_id, {'event': 'test_completed', **step, 'evidence_id': eid,
                                 'evidence_path': name, **result})
        if (result.get('phase') != phase or result.get('code_version') != expected_version
                or code_version(work, (self.source,)) != expected_version
                or (result.get('test_suite_version') is not None
                    and result['test_suite_version'] != self.final['test_suite_version'])):
            raise IsolationBlocked('执行后证据版本不一致')
        # Fail closed: a runner that omits the binding flag proves nothing.
        self.final['evidence_matches_current_version'] = result.get('evidence_matches_current_version') is True
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
        lines = []
        for line in text.splitlines():
            if line.lstrip().startswith(('CODEFIX_REPORT:', 'E ', '> ', 'assert ', 'AssertionError')):
                continue
            # Preserve only allowlisted non-assertion types, never messages.
            if line.lstrip().startswith(('FAILED ', 'ERROR ')) and ' - ' in line:
                match = _exception_header(line)
                detail = match.group(1) if match and match.group(1) != 'AssertionError' else '[details redacted]'
                line = line.split(' - ', 1)[0] + ' - ' + detail
            lines.append(re.sub(r'\[[^\]\n]*\]', '[parameters redacted]', line))
        return '\n'.join(lines)

    @staticmethod
    def diagnostics(result, folder):
        """Extract allowlisted types/module identifiers, never arbitrary messages."""
        exception_types = []
        missing_modules = []
        for name in ('stdout_path', 'stderr_path'):
            path = folder / result.get(name, 'missing')
            if not path.is_file():
                continue
            with path.open('rb') as stream:
                text = stream.read(8000).decode('utf-8', errors='replace')
            for line in text.splitlines():
                # Accept pytest E/summary lines and explicit ErrorType headers;
                # assert lines, source lines and traceback frames never match.
                match = _exception_header(line)
                if not match:
                    continue
                exc_type = match.group(1)
                if exc_type not in exception_types:
                    exception_types.append(exc_type)
                if exc_type not in {'ImportError', 'ModuleNotFoundError'}:
                    continue
                module = re.fullmatch(
                    r'''\s*No module named ['"]([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)['"]\s*''',
                    match.string[match.end():])
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
        correction = None
        for attempt in range(2 if role in {'detector', 'scheduler', 'locator'} else 1):
            material = {'previous_analysis': extra, 'output_validation_error': correction} if correction else extra
            if role == 'repairer' and self.output_feedback:
                material = {'location': extra, 'output_validation_error': self.output_feedback}
            try:
                value = self._call_once(role, ev, round_number, material)
                self.empty_reasoning_streak[role] = 0
                if role == 'repairer':
                    self.output_feedback = None
                return value
            except InvalidRoleOutput as exc:
                empty = exc.diagnostics.get('kind') == 'reasoning_budget_exhausted'
                self.empty_reasoning_streak[role] = self.empty_reasoning_streak.get(role, 0) + 1 if empty else 0
                exc.repeated_empty = self.empty_reasoning_streak[role] >= 2
                if attempt or exc.repeated_empty or role not in {'detector', 'scheduler', 'locator'}:
                    raise
                correction = str(exc)
                self.store.event(self.run_id, {'event': 'format_retry', 'role': role,
                    'round': round_number, 'reason': correction, 'remaining_format_retries': 0})

    def _call_once(self, role, ev, round_number, extra=None):
        self.boundary()
        if self.model_calls >= self.task['max_calls']:
            raise ModelBlocked('模型调用次数到达上限')
        step = self.trace.start(role, round_number=round_number, evidence_ids=[ev['evidence_id']])
        resumed_loop = self.pending_loop if role == 'repairer' else None
        if resumed_loop:
            self.pending_loop = None
            self.store.event(self.run_id, {'event': 'self_loop_resumed', 'step_id': step['step_id'],
                'role': role, 'round': round_number, 'code_version': self.version,
                'evidence_ids': [ev['evidence_id']], **resumed_loop})
        self.model_calls += 1
        from .agent_team import AgentHandoffBlocked
        try:
            record = self.gateway.invoke(role, run_id=self.run_id, round_number=round_number,
                                code=(self.current / self.source).read_text(encoding='utf-8'), evidence=ev,
                                version=self.version, work=self.current, task=self.task, deadline=self.deadline, extra=extra,
                                trace={k: step[k] for k in ('step_id', 'parent_step_id')},
                                handoffs=[self.agent_outputs[sender]
                                    for sender in self.gateway.agents[role].receives_from
                                    if sender in self.agent_outputs
                                    and self.agent_outputs[sender]['code_version'] == self.version
                                    and self.agent_outputs[sender]['evidence_id'] == ev['evidence_id']])
        except AgentHandoffBlocked as exc:
            self.final['failure_class'] = 'agent_handoff_blocked'
            self.trace.complete('blocked', reason=str(exc), code_version=self.version,
                                failure_class='agent_handoff_blocked', model_api_called=False)
            raise ModelBlocked(str(exc)) from exc
        self.final['model_api_called'] |= record.get('model_api_called', False)
        self.actual_requests += int(record.get('model_api_called') is True)
        budget = record.get('cost', {}).get('budget')
        if budget and budget.get('alerts'):
            self.store.event(self.run_id, {'event': 'budget_alert', 'step_id': step['step_id'],
                                     'budget': budget, 'reason': '人民币账号额度提醒；非平台实时余额'})
        self.trace.complete(record['status'], call_id=record['call_id'],
                       artifact_path='calls/' + record['call_id'],
                       code_version=self.version, failure_class=record.get('failure_class'),
                       agent_instance_id=record['provenance']['agent_instance_id'],
                       agent_contract=record['provenance']['agent_contract'],
                       handoffs=record['provenance']['handoffs'], resumed_loop=resumed_loop)
        # A stop/pause received while the model was in flight must be
        # observed before applying its output or starting another step.
        self.boundary()
        if record['status'] != 'ok':
            self.final['failure_class'] = record.get('failure_class', 'unknown')
            if self.final['failure_class'] == 'invalid_model_output':
                raise InvalidRoleOutput(role, record.get('reason') or '输出不符合角色JSON协议', record.get('output_diagnostics'))
            elif self.final['failure_class'] in {'permission_violation', 'model_transport_error'}:
                self.final['guard_id'] = 'V3_' + self.final['failure_class'].upper()
                routed = self.route(self.final['failure_class'])
                raise RoutedFailure('paused' if routed['action'] == 'pause' else 'stopped', record.get('reason', routed['reason']))
            raise ModelBlocked('角色调用失败：' + record.get('failure_class', 'unknown')
                               + ('；' + record['reason'] if record.get('reason') else ''))
        from .role_config import digest
        self.agent_outputs[role] = {'agent_id': role, 'call_id': record['call_id'],
            'step_id': step['step_id'], 'artifact_path': 'calls/' + record['call_id'],
            'code_version': self.version, 'evidence_id': ev['evidence_id'],
            'output': record['parsed_output'], 'output_sha256': digest(record['parsed_output'])}
        return record['parsed_output']

    def prepare(self):
        self.boundary()
        if self.definition.admission_blocker:
            return self.finish('blocked', self.definition.admission_blocker)
        if self.task.get('config_id'):
            from .configuration import ConfigurationStore
            config = ConfigurationStore(self.store).runtime(self.task['config_id'])
        else:
            config = RoleConfig(ROOT)
        try:
            config.preflight()
        except (ConfigError, ModelBlocked) as exc:
            self.store.event(self.run_id, {'event': 'guard_check', 'step_id': 'configuration-check',
                'node_id': 'configuration_check', 'status': 'blocked', 'reason': str(exc)})
            raise
        self.store.event(self.run_id, {'event': 'guard_check', 'step_id': 'configuration-check',
            'node_id': 'configuration_check', 'status': 'ok', 'reason': '角色配置、提示词和输出协议检查通过'})
        from .experiment_identity import freeze
        self.trace.write('trace/experiment-preflight.json', freeze(ROOT, self.task, self.definition, config, self.runner, self.model))
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
        # Keep the initial inputs separately. The probe resolves the cloud image
        # and build; write the final identity once, before any model request.
        self.trace.write('trace/experiment.json', freeze(ROOT, self.task, self.definition, config, self.runner, self.model))
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
                    'strategy': self.strategy, 'role_overrides': config.role_overrides,
                    'runtime_limits': read_json(ROOT / 'configs/runtime_limits.json'),
                    'bindings': {role: self.model.binding(role) for role in config.agents}
                    if hasattr(self.model, 'binding') else {'mode': getattr(self.model, 'mode', 'unknown')},
                    'rules': rules,
                    'production_guards': RUNTIME_POLICY,
                    'model_profiles': getattr(self.model, 'registry', None),
                    'provenance': {role: config.provenance(role) for role in config.agents}})
        self.gateway = AgentGateway(self.model, self.store, config)
        self.trace.write('trace/agent-team.json', {'schema_version': '1.0.0',
            'run_id': self.run_id, 'strategy': self.strategy,
            'active_agents': ['repairer'] if self.strategy == 'single' else list(self.gateway.agents),
            'agents': {role: {**agent.contract(config), 'agent_instance_id': f'{self.run_id}:{role}'}
                       for role, agent in self.gateway.agents.items()},
            'execution_authority': 'program_rules_and_verified_tool_evidence',
            'loop_limits': {k: self.task[k] for k in ('max_rounds', 'max_calls', 'total_seconds')},
            'shared_cost_guard': True, 'final_feedback_to_agents': False})
        self.engine = DecisionEngine(rules)
        self.base = self.definition.prepare(self.directory / 'base')
        self.current = self.base
        self.version = code_version(self.current, (self.source,))
        self.final.update(code_version=self.version, code_path='base/' + self.source)
        self.location = None

    def final_check(self):
        # Replay full final source on original clean input, never reuse development workspace.
        final_work = self.directory / 'final-work'
        payload = (self.current / self.source).read_bytes()
        if code_version(self.current, (self.source,)) != self.version:
            raise IsolationBlocked('最终候选版本已改变')
        record = apply_candidate(self.base, final_work,
                                 [{'path': self.source, 'new_content': payload.decode('utf-8')}],
                                 self.directory / 'final-patch', project=self.task['project'])
        if ((final_work / self.source).read_bytes() != payload
                or record['candidate_version'] != self.version):
            raise IsolationBlocked('最终验收字节与候选版本不一致')
        checked, _ = self.test(final_work, 'final_check', 'final-check')
        self.final['final_result'] = checked['verification_status']
        self.final['failure_class'] = checked['failure_class']
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
        # Only usable tool results enter model analysis. Environment, version
        # and permission failures retain their immediate protective exits.
        detector = (self.call('detector', ev, round_number)
                    if self.strategy != 'single' and result['failure_class'] in {'passed', 'assertion_failed', 'syntax_error', 'code_runtime_error'}
                    else None)
        # Terminal safety branches take precedence over all model advice.
        context = dict(phase='development', failure_class=result['failure_class'], isolation_ready=self.final['isolation_ready'],
                       user_pause_requested=False, evidence_matches_current_version=self.final.get('evidence_matches_current_version', False),
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
        # Each usable candidate returns to the detector after real tool testing.
        # The detector interprets evidence; it never replaces the executor.
        # Advisory model calls must not consume the last chance to
        # validate an already passing candidate with the tool executor.
        advice, skipped = None, None
        if self.strategy == 'single':
            skipped = '单Agent对照：仅修复员，依据相同隔离测试反馈有界重试；权限、预算及干净复验保持'
        elif decision['action'] == 'final_check':
            skipped = '隔离测试与检测分析完成；跳过非必需模型调度建议，进入干净复验'
        elif self.strategy == 'dynamic' and result['failure_class'] == 'syntax_error':
            skipped = '动态策略：检测确认语法失败，直接重修，跳过模型调度建议与定位'
        else:
            advice = self.call('scheduler', ev, round_number, detector)
        planned = 'repair' if self.strategy in {'fixed', 'single'} and decision['action'] == 'locate' else decision['action']
        effective = advice['action'] if advice and advice['action'] in {'pause', 'stop'} else planned
        reason = advice['reason'] if advice and effective in {'pause', 'stop'} else decision['reason']
        overrides = []
        if advice and advice['action'] != planned:
            overrides.append('模型要求保守停止，程序允许停止；不采纳推进动作'
                             if effective in {'pause', 'stop'} else '模型建议不能覆盖工具证据与强制规则')
        if planned != decision['action']:
            overrides.append('单Agent对照仅允许修复员重试' if self.strategy == 'single' else '固定策略不追加定位员；保留共同保护，失败时下一轮按固定顺序重修')
        if skipped:
            overrides.append(skipped)
            self.store.event(self.run_id, {'event': 'roles_skipped', 'roles': ['detector', 'scheduler', 'locator'] if self.strategy == 'single' else ['scheduler'],
                'round': round_number, 'reason': skipped, 'code_version': self.version})
        override = '；'.join(overrides) or None
        if effective == 'locate' and self.model_calls >= self.task['max_calls']:
            effective, reason = 'stop', '模型调用次数到达上限'
            override = '定位需要新增模型调用；剩余调用预算不足，停止调度'
            self.final['guard_id'] = 'V3_MODEL_CALL_LIMIT'
        dispatch_record = self.trace.decision(program=decision, effective_action=effective, reason=reason,
                       advice=advice, override_reason=override,
                       evidence_ids=[ev['evidence_id']], version=self.version,
                       suite_version=self.final['test_suite_version'], counters=self.counters(), round_number=round_number,
                       self_loop=({'status': 'planned', 'from_round': round_number,
                           'to_round': round_number + 1, 'failure_class': result['failure_class'],
                           'via': ['locator', 'repairer'] if effective == 'locate' else ['repairer'],
                           'evidence_id': ev['evidence_id'], 'code_version': self.version,
                           'remaining_rounds': self.task['max_rounds'] - round_number}
                           if effective in {'locate', 'repair'} else None))
        if self.final.get('guard_id') == 'V3_MODEL_CALL_LIMIT':
            return result, folder, self.finish('blocked', reason)
        if advice and advice['action'] in {'pause', 'stop'}:
            return result, folder, self.finish('paused' if advice['action'] == 'pause' else 'stopped', advice['reason'])
        if decision['action'] == 'final_check':
            return result, folder, self.final_check()
        # A model cannot force final validation of failing code or bypass syntax rules.
        if effective == 'locate':
            self.agent_outputs['scheduler']['dispatch_instruction'] = {
                'effective_action': 'locate', 'target_agent': 'locator',
                'rule_id': decision['rule_id'], 'reason': reason,
                'decision_id': dispatch_record['decision_id'],
                'artifact_path': 'trace/decisions/' + dispatch_record['decision_id'] + '.json',
                'code_version': self.version, 'evidence_id': ev['evidence_id']}
            try:
                self.location = self.call('locator', ev, round_number)
            except InvalidRoleOutput as exc:
                self.location = None
                self.output_feedback = '定位员未返回有效定位结果，请依据当前开发测试证据自行定位并修复。' + str(exc)
                self.store.event(self.run_id, {'event': 'locator_fallback', 'round': round_number,
                    'reason': str(exc), 'effective_action': 'repair', 'code_version': self.version,
                    'evidence_ids': [ev['evidence_id']]})
                self.trace.decision(program={'action': 'repair', 'rule_id': 'locator_output_fallback',
                    'reason': '定位输出纠正后仍无效，保留失败并回到修复'}, effective_action='repair',
                    reason='定位失败；下一轮使用同一候选的开发证据，不伪造定位结果',
                    evidence_ids=[ev['evidence_id']], version=self.version,
                    suite_version=self.final['test_suite_version'], counters=self.counters(), round_number=round_number)
        else:
            self.location = None
        self.pending_loop = {'from_round': round_number, 'to_round': round_number + 1,
            'trigger_evidence_id': ev['evidence_id'], 'trigger_code_version': self.version}
        return result, folder, None

    def run(self):
        try:
            terminal = self.prepare()
            if terminal is not None:
                return terminal
            result, folder = self.test(self.base, 'development', 'baseline')
            self.final['dev_result'] = result['verification_status']
            self.boundary()
            baseline_failure = result.get('failure_class', 'unknown_evidence')
            self.final['baseline_failure_class'] = baseline_failure
            if baseline_failure not in BASELINE_FAILURE_CLASSES:
                routed = self.route('not_reproduced' if baseline_failure == 'passed' else baseline_failure)
                return self.finish('stopped' if routed['action'] == 'stop' else 'paused', routed['reason'])
            for round_number in range(1, self.task['max_rounds'] + 1):
                try:
                    result, folder, terminal = self.repair_round(result, folder, round_number)
                except InvalidRoleOutput as exc:
                    if exc.repeated_empty:
                        self.final['model_recovery'] = {'role': exc.role, 'kind': 'repeated_reasoning_exhaustion',
                            'automatic_model_switch': False, 'requires_new_task': True,
                            'suggested_action': '调整已支持的思考模式，或选择已配置的代码模型；保存新配置后创建新任务。'}
                        return self.finish('blocked', f'{exc.role}连续两次耗尽思考额度且正文为空，停止原样重试并移交人工')
                    if exc.role != 'repairer':
                        return self.finish('blocked', f'{exc.role}格式纠正后仍无效：{exc}')
                    self.output_feedback = str(exc)
                    self.store.event(self.run_id, {'event': 'format_retry', 'role': exc.role,
                        'round': round_number, 'reason': str(exc),
                        'remaining_rounds': self.task['max_rounds'] - round_number})
                    continue
                if terminal is not None:
                    return terminal
            return self.finish('stopped', '达到最大修复轮次')
        except ControlRequested as exc:
            self.final['failure_class'] = 'user_pause' if exc.action == 'pause' else 'user_stop'
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


class InvalidRoleOutput(Exception):
    def __init__(self, role, reason, diagnostics=None):
        self.role = role
        self.diagnostics = diagnostics or {}
        self.repeated_empty = False
        super().__init__(reason)


class RoutedFailure(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
