"""Public-dataset baseline: Docker only, with no model calls or host fallback."""
import time

from .docker_runner import DockerRunner
from .runner import code_version
from .tasks_v3 import get_task


def run_public_baseline(store, run_id, runner=None):
    run = store.get(run_id)
    task = get_task(run['task']['project'])
    runner = runner or DockerRunner(task.project)
    directory = store.directory(run_id)
    deadline = time.monotonic() + run['task']['total_seconds']
    result = {'project': task.project, 'mode': 'baseline', 'model_api_called': False, 'model_calls': 0,
              'tool_calls': 0, 'final_result': 'not_run', 'dev_result': 'not_run',
              'test_suite_version': task.summary()['test_suite_version'], 'isolation_ready': False}

    def boundary():
        action = store.get(run_id)['control']
        if action:
            return ('paused' if action == 'pause' else 'stopped', result | {'reason': '按用户请求在步骤边界停止推进'})
        if time.monotonic() >= deadline:
            return 'blocked', result | {'reason': '任务总时限到达；不启动后续测试'}
        return None

    if stopped := boundary():
        return stopped
    if task.admission_blocker:
        return 'blocked', result | {'reason': task.admission_blocker}
    isolation = runner.preflight(directory / 'isolation')
    store.event(run_id, {'event': 'isolation_checked', **isolation})
    result['isolation_ready'] = isolation['isolation_ready']
    if stopped := boundary():
        return stopped
    if not isolation['isolation_ready']:
        return 'blocked', result | {'reason': '公开题目必须隔离执行；' + isolation.get('reason', 'Docker尚未就绪')}
    work = task.prepare(directory / 'base')
    if stopped := boundary():
        return stopped
    result['tool_calls'] = 1
    folder = directory / 'baseline'
    tested = runner.run(work, folder, phase='development', timeout=max(.01, min(60, deadline-time.monotonic())),
                        expected_version=code_version(work, (task.source,)))
    if tested.get('code_version') != code_version(work, (task.source,)):
        raise ValueError('公开题目测试证据版本不一致')
    store.event(run_id, {'event': 'test_completed', 'evidence_path': 'baseline', **tested})
    result.update(tool_result=tested, dev_result=tested['verification_status'], code_version=tested['code_version'],
                  reason='公开题目原始基线检查；没有调用模型或独立验收')
    log = folder / tested.get('stdout_path', 'missing')
    if log.is_file():
        with log.open('rb') as stream:
            text = stream.read(12000).decode('utf-8', errors='replace')
        result['stdout_excerpt'] = '\n'.join(line for line in text.splitlines() if not line.startswith('CODEFIX_REPORT:'))
    if stopped := boundary():
        return stopped
    return ('blocked' if tested['verification_status'] in {'not_run', 'inconclusive'} else 'stopped'), result
