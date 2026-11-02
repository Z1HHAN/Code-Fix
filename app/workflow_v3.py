"""Evidence-driven repair state machine; final-check feedback never enters agents."""
import json
from pathlib import Path
import time

from .agents_v3 import AgentGateway
from .candidates import apply_candidate
from .config import read_json
from .decision import DecisionEngine
from .docker_runner import DockerRunner, IsolationBlocked
from .live_model import Ledger, LiveModel, LiveSettings, ModelBlocked
from .runner import code_version
from .tasks_v3 import get_task

ROOT = Path(__file__).resolve().parents[1]


class Workflow:
    def __init__(self, store, runner=None, model=None):
        self.store = store
        self.runner = runner
        self.model = model
        self.model_calls = 0
        self.tool_calls = 0

    def run(self, run_id):
        task = self.store.get(run_id)['task']
        definition = get_task(task['project'])
        source = definition.source
        self.runner = self.runner or DockerRunner(task['project'])
        directory = self.store.directory(run_id)
        deadline = time.monotonic() + task['total_seconds']
        final = {'final_result': 'not_run', 'dev_result': 'not_run', 'rounds_used': 0,
                 'model_calls': 0, 'tool_calls': 0, 'model_api_called': False,
                 'mode': getattr(self.model, 'mode', 'live'), 'isolation_ready': False,
                 'project': task['project'], 'test_suite_version': definition.summary()['test_suite_version']}
        def finish(status, reason):
            final.update(reason=reason, model_calls=self.model_calls, tool_calls=self.tool_calls)
            return status, final

        def boundary():
            control = self.store.get(run_id)['control']
            if control:
                raise ControlRequested(control)
            if time.monotonic() >= deadline:
                raise ModelBlocked('任务总时限到达')

        try:
            boundary()
            settings = None
            blockers = []
            if self.model is None:
                try:
                    settings = LiveSettings.from_env()
                except ModelBlocked as exc:
                    blockers.append(str(exc))
            isolation = self.runner.preflight(directory / 'isolation')
            self.store.event(run_id, {'event': 'isolation_checked', **isolation})
            final['isolation_ready'] = isolation['isolation_ready']
            boundary()
            if not isolation['isolation_ready']:
                blockers.append(isolation.get('reason', '隔离未就绪'))
            if blockers:
                final['blockers'] = blockers
                return finish('blocked', '运行前置条件未满足；未发起模型请求或候选执行')
            if self.model is None:
                self.model = LiveModel(settings, Ledger(self.store.root))
            gateway = AgentGateway(self.model, self.store)
            engine = DecisionEngine(read_json(ROOT / 'configs/decision_rules.json'))
            base = definition.prepare(directory / 'base')
            current = base
            version = code_version(current, (source,))
            location = None

            def test(work, phase, name):
                boundary()
                self.tool_calls += 1
                folder = directory / name
                result = self.runner.run(work, folder, phase=phase,
                                         timeout=max(.01, min(60, deadline-time.monotonic())),
                                         expected_version=code_version(work, (source,)))
                if result.get('code_version') != code_version(work, (source,)):
                    raise IsolationBlocked('执行后证据版本不一致')
                self.store.event(run_id, {'event': 'test_completed', 'evidence_path': name, **result})
                return result, folder

            def evidence(result, folder, number):
                # No full history, secrets, reference implementation or final evidence.
                def excerpt(name):
                    path = folder / result.get(name, 'missing')
                    if not path.is_file():
                        return ''
                    with path.open('rb') as stream:
                        text = stream.read(6000).decode('utf-8', errors='replace')
                    return '\n'.join(line for line in text.splitlines() if not line.startswith('CODEFIX_REPORT:'))
                summary = {'counts': result.get('counts'), 'exit_code': result.get('exit_code'),
                           'failure_class': result['failure_class'], 'stdout': excerpt('stdout_path'),
                           'stderr': excerpt('stderr_path')}
                return {'evidence_id': f'dev-{number}', 'phase': 'development', 'code_version': result['code_version'],
                        'failure_class': result['failure_class'], 'summary': json.dumps(summary, ensure_ascii=False)}

            def call(role, ev, round_number, extra=None):
                boundary()
                if self.model_calls >= task['max_calls']:
                    raise ModelBlocked('模型调用次数到达上限')
                self.model_calls += 1
                record = gateway.invoke(role, run_id=run_id, round_number=round_number,
                                        code=(current / source).read_text(encoding='utf-8'), evidence=ev,
                                        version=version, work=current, task=task, deadline=deadline, extra=extra)
                final['model_api_called'] |= record.get('model_api_called', False)
                # A stop/pause received while the model was in flight must be
                # observed before applying its output or starting another step.
                boundary()
                if record['status'] != 'ok':
                    raise ModelBlocked('角色调用失败：' + record.get('failure_class', 'unknown'))
                return record['parsed_output']

            result, folder = test(base, 'development', 'baseline')
            boundary()
            if result.get('failure_class') != 'assertion_failed':
                return finish('blocked', '原始缺陷未按预期复现；不算模型修复')
            for round_number in range(1, task['max_rounds'] + 1):
                ev = evidence(result, folder, round_number)
                final['rounds_used'] = round_number
                candidate = call('repairer', ev, round_number, location)
                target = directory / f'candidate-{round_number}'
                patch = apply_candidate(current, target, candidate['changes'], directory / f'patch-{round_number}', project=task['project'])
                current, version = target, patch['candidate_version']
                self.store.event(run_id, {'event': 'candidate_applied', 'round': round_number, **patch})
                result, folder = test(current, 'development', f'development-{round_number}')
                final.update(dev_result=result['verification_status'], code_version=version)
                boundary()
                ev = evidence(result, folder, round_number + 1)
                # Terminal safety branches take precedence over all model advice.
                context = dict(phase='development', failure_class=result['failure_class'], isolation_ready=True,
                               user_pause_requested=False, evidence_matches_current_version=True,
                               repair_budget_available=round_number < task['max_rounds'],
                               execution_budget_available=time.monotonic() < deadline,
                               needs_new_repair=result['failure_class'] != 'passed',
                               next_action_requires_execution_budget=True, output_agent=None,
                               retries_left=task['max_rounds']-round_number)
                decision = engine.decide(context, run_id=run_id, code_version=version,
                                         evidence_ids=[ev['evidence_id']], decision_id=f'd-{round_number}')
                if decision['action'] in {'pause', 'stop'}:
                    self.store.event(run_id, {'event': 'decision', 'round': round_number, **decision})
                    return finish('paused' if decision['action'] == 'pause' else 'stopped', decision['reason'])
                detector = call('detector', ev, round_number)
                advice = call('scheduler', ev, round_number, detector)
                self.store.event(run_id, {'event': 'decision', 'round': round_number,
                                         'model_advice': advice, **decision})
                if advice['action'] in {'pause', 'stop'}:
                    return finish('paused' if advice['action'] == 'pause' else 'stopped', advice['reason'])
                if decision['action'] == 'final_check':
                    # Replay full final source on original clean input, never reuse development workspace.
                    final_work = directory / 'final-work'
                    apply_candidate(base, final_work, [{'path': source, 'new_content': (current / source).read_text(encoding='utf-8')}],
                                    directory / 'final-patch', project=task['project'])
                    checked, _ = test(final_work, 'final_check', 'final-check')
                    final['final_result'] = checked['verification_status']
                    boundary()
                    if checked['verification_status'] == 'passed' and checked['failure_class'] == 'passed':
                        return finish('success', '开发测试及干净副本同套原生测试均实际通过')
                    return finish('failed', '独立验收未通过；结果不回流本轮Agent')
                # A model cannot force final validation of failing code or bypass syntax rules.
                if decision['action'] == 'locate' or (decision['action'] == 'repair' and advice['action'] == 'locate'):
                    location = call('locator', ev, round_number)
                else:
                    location = None
            return finish('stopped', '达到最大修复轮次')
        except ControlRequested as exc:
            return finish('paused' if exc.action == 'pause' else 'stopped', '按用户请求在步骤边界停止推进')
        except (ModelBlocked, IsolationBlocked) as exc:
            return finish('blocked', str(exc))
        except (OSError, ValueError) as exc:
            final['error_type'] = type(exc).__name__
            return finish('failed', '输出、补丁或执行证据无效，已停止')


class ControlRequested(Exception):
    def __init__(self, action):
        self.action = action
