"""第4项：超时与运行中停止的真实 Docker 实测。

三个场景，全部在真实 Docker 内执行，不使用离线替身：

A  timeout-single  单容器 runner（app.docker_runner.DockerRunner）跑一个
                   "模块导入即死循环"的候选，验证 runner 在 timeout 到点后
                   强杀容器、删除 /out 卷并返回，不无限等待。
A2 timeout-split   双容器 runner（app.split_runner.SplitDockerRunner）跑一个
                   "函数内死循环"的候选，验证可信测试侧按单次 RPC 上限收敛，
                   runner 在有限时间内返回并完成清理。
B  operator-stop   真实 Workflow + 真实 SplitDockerRunner + RunStore 控制面，
                   在开发轮容器运行中调用 store.interrupt()，验证容器/卷清理，
                   且停止后不再派发任何后续阶段（无 final_check）。

用法：
    $env:CODE_FIX_EXECUTION_APPROVED='1'
    $env:CODE_FIX_DOCKER_IMAGE='sha256:<镜像ID>'
    python scripts/docker_timeout_and_stop.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.docker_runner import DockerRunner  # noqa: E402
from app.run_store import RunStore  # noqa: E402
from app.runner import code_version  # noqa: E402
from app.split_runner import SplitDockerRunner  # noqa: E402
from app.tasks_v3 import get_task  # noqa: E402
from app.workflow_v3 import Workflow  # noqa: E402

TZ = timezone(timedelta(hours=8))
DOCKER = shutil.which('docker') or 'docker'

# 夹具来自 tests/fixtures/runtime/：脚本只读字节，不在代码里内嵌伪造源。
FIXTURE_DIR = ROOT / 'tests/fixtures/runtime'
SOURCE_HANG_ON_IMPORT = (FIXTURE_DIR / 'hang_on_import.py').read_text(encoding='utf-8')
SOURCE_HANG_IN_CALL = (FIXTURE_DIR / 'hang_in_call.py').read_text(encoding='utf-8')


def docker(*args, timeout=60):
    return subprocess.run([DOCKER, *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def codefix_containers():
    got = docker('ps', '-a', '--filter', 'name=codefix-', '--format', '{{.Names}}|{{.Status}}|{{.Image}}')
    return [x for x in got.stdout.splitlines() if x.strip()]


def codefix_volumes():
    got = docker('volume', 'ls', '--filter', 'name=codefix-', '--format', '{{.Name}}')
    return [x for x in got.stdout.splitlines() if x.strip()]


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    else:
        text = str(value)
    path.write_text(text, encoding='utf-8')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class Sampler:
    """后台采样 docker ps，证明容器在超时/停止发生时确实存在过。"""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._started = time.monotonic()

    def _loop(self):
        while not self._stop.is_set():
            try:
                snapshot = codefix_containers()
            except Exception as exc:  # noqa: BLE001
                snapshot = ['<probe error: %s>' % type(exc).__name__]
            self.samples.append({'offset_s': round(time.monotonic() - self._started, 2),
                                 'containers': snapshot})
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)
        return False


def describe(samples):
    seen = sorted({row.split('|')[0] for s in samples for row in s['containers'] if '|' in row})
    return seen


# ---------------------------------------------------------------- 场景 A / A2

def scenario_timeout(run_dir: Path, timeout: float, split: bool) -> dict:
    task = get_task('leap')
    work = task.prepare(run_dir / 'workspace')
    (work / task.source).write_text(SOURCE_HANG_IN_CALL if split else SOURCE_HANG_ON_IMPORT,
                                    encoding='utf-8', newline='\n')
    version = code_version(work, (task.source,))
    save(run_dir / 'candidate-source.py', (work / task.source).read_text(encoding='utf-8'))

    before = {'containers': codefix_containers(), 'volumes': codefix_volumes()}
    save(run_dir / 'docker-before.json', before)

    runner = SplitDockerRunner('leap') if split else DockerRunner('leap')
    record = {'mode': 'split' if split else 'single',
              'runner_class': type(runner).__name__, 'timeout_seconds': timeout,
              'candidate_sha256': sha256_file(work / task.source)}
    started = time.monotonic()
    with Sampler() as sampler:
        try:
            preflight = runner.preflight(run_dir / 'isolation')
            record['isolation_ready'] = preflight.get('isolation_ready')
            record['isolation_reason'] = preflight.get('reason')
            if not preflight.get('isolation_ready'):
                record.update(ok=False, error='preflight failed')
            else:
                result = runner.run(work, run_dir / 'run', phase='baseline',
                                    timeout=timeout, expected_version=version)
                record['result'] = result
        except Exception as exc:  # noqa: BLE001
            record['exception'] = type(exc).__name__ + ': ' + str(exc)
            record['traceback'] = traceback.format_exc()[-4000:]
    elapsed = round(time.monotonic() - started, 2)
    record['elapsed_seconds'] = elapsed
    record['containers_seen'] = describe(sampler.samples)
    save(run_dir / 'container-timeline.json', sampler.samples)

    # 给 Docker 一点时间完成删除，再取证。
    time.sleep(2)
    after = {'containers': codefix_containers(), 'volumes': codefix_volumes()}
    save(run_dir / 'docker-after.json', after)
    record['leftover_containers'] = after['containers']
    record['leftover_volumes'] = after['volumes']
    save(run_dir / 'after-ps-a.txt', '\n'.join(after['containers']) or '<none>')

    run_result = (run_dir / 'run' / 'result.json')
    record['result_present'] = run_result.is_file()
    if run_result.is_file():
        record['result_json'] = json.loads(run_result.read_text(encoding='utf-8'))
    cleanup = run_dir / 'run' / 'cleanup.json'
    record['cleanup_json'] = json.loads(cleanup.read_text(encoding='utf-8')) if cleanup.is_file() else None

    res = record.get('result_json') or {}
    record['criteria'] = {
        'bounded_time': elapsed <= timeout + 30,
        'timed_out_flag': res.get('timed_out') is True,
        'status_timeout': res.get('status') == 'timeout',
        'failure_class_timeout': res.get('failure_class') == 'timeout',
        'cleanup_passed': res.get('cleanup_passed') is True,
        'container_really_ran': bool(record['containers_seen']),
        'no_container_left': not after['containers'],
        'no_volume_left': not after['volumes'],
    } if not split else {
        'bounded_time': elapsed <= timeout + 30,
        'returned_status': res.get('status'),
        'not_passed': res.get('status') != 'passed',
        'cleanup_passed': res.get('cleanup_passed') is True,
        'container_really_ran': bool(record['containers_seen']),
        'no_container_left': not after['containers'],
        'no_volume_left': not after['volumes'],
    }
    record['ok'] = all(v is True for k, v in record['criteria'].items()
                       if isinstance(v, bool)) and bool(record['criteria'])
    save(run_dir / 'timeout-record.json', record)
    return record


# ---------------------------------------------------------------- 场景 B

class HangingRepairModel:
    """模型替身：只负责返回一个"函数内死循环"的候选补丁。

    模型不是本项被测对象；Docker 执行、取消、清理全部走真实实现。
    """

    mode = 'deterministic_stub_for_stop_test'

    def __init__(self):
        self.roles = []
        self.repairer_called_at = None

    def call(self, messages, **kwargs):
        prompt = messages[0]['content']
        material = json.loads(messages[1]['content'])
        source, content = next(iter(material['current_code'].items()))
        evidence = material['dev_evidence'][0]
        common = {'evidence_ids': [evidence['evidence_id']], 'knowledge_citations': []}
        if '定位员提示词' in prompt:
            role = 'locator'
            output = common | {'file': source, 'function': 'is_leap', 'hypothesis': '停止测试占位定位'}
        elif 'V3检测员' in prompt:
            role = 'detector'
            output = common | {'analysis': 'stop-test placeholder', 'failure_class': evidence['failure_class'],
                               'suggested_checks': []}
        elif 'V3调度员' in prompt:
            role = 'scheduler'
            output = common | {'action': 'repair', 'reason': 'stop-test placeholder'}
        else:
            role = 'repairer'
            self.repairer_called_at = time.monotonic()
            output = common | {'explanation': '停止测试候选：函数体内死循环',
                               'changes': [{'path': source, 'new_content': SOURCE_HANG_IN_CALL}]}
        self.roles.append(role)
        return {'status': 'ok', 'mode': self.mode, 'model_api_called': False,
                'raw_text': json.dumps(output, ensure_ascii=False)}


def scenario_stop(run_dir: Path, hard_cap: float) -> dict:
    store = RunStore(run_dir / 'store')
    run_id = store.create({'mode': 'repair', 'project': 'leap',
                           'description': '运行中停止测试：开发轮执行期间由操作员中断'})['run_id']
    store.claim(run_id)
    model = HangingRepairModel()
    runner = SplitDockerRunner('leap')
    workflow = Workflow(store, runner, model)

    box, timeline = {}, []
    t0 = time.monotonic()

    def mark(event, **detail):
        timeline.append({'offset_s': round(time.monotonic() - t0, 2), 'event': event, **detail})

    def target():
        try:
            status, result = workflow.run(run_id)
            box['status'], box['result'] = status, result
            # 与 TaskService.run_claimed 一致：worker 收尾不得覆盖操作员的终态。
            box['finish_if_running'] = store.finish_if_running(run_id, status, result)
        except BaseException as exc:  # noqa: BLE001
            box['error'] = type(exc).__name__ + ': ' + str(exc)
            box['traceback'] = traceback.format_exc()[-4000:]
        mark('workflow_returned')

    before = {'containers': codefix_containers(), 'volumes': codefix_volumes()}
    save(run_dir / 'docker-before.json', before)
    mark('workflow_start', run_id=run_id)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    at_stop = {}
    first_running_at = None
    mark('stop_armed')
    while thread.is_alive():
        alive_for = time.monotonic() - t0
        if model.repairer_called_at is not None and not at_stop:
            running = [x for x in codefix_containers() if '|Up' in x or '|Restarting' in x]
            if running and first_running_at is None:
                first_running_at = time.monotonic()
                mark('development_containers_up', containers=running)
            # 等到开发轮容器真正跑起来 2.5s 再中断，确保是"跑到一半"而不是刚启动。
            if running and first_running_at is not None and time.monotonic() - first_running_at >= 2.5:
                at_stop = {'containers': running, 'volumes': codefix_volumes(),
                           'offset_s': round(time.monotonic() - t0, 2)}
                save(run_dir / 'containers-at-stop.txt', '\n'.join(running))
                save(run_dir / 'volumes-at-stop.txt', '\n'.join(at_stop['volumes']) or '<none>')
                mark('interrupt_sent', containers=running, volumes=at_stop['volumes'])
                store.interrupt(run_id)
                mark('interrupt_recorded', effective=store.get(run_id)['status'])
        if alive_for >= hard_cap and not at_stop:
            at_stop = {'containers': codefix_containers(), 'volumes': codefix_volumes(),
                       'offset_s': round(time.monotonic() - t0, 2), 'forced': True}
            mark('hard_cap_interrupt')
            store.interrupt(run_id)
        time.sleep(0.25)

    thread.join(timeout=60)
    mark('observed_done')
    time.sleep(2)

    final = store.get(run_id)
    events = store.events(run_id, 0, 500)
    save(run_dir / 'store-events.json', events)
    # store.events() 已把 payload 字段展开到顶层，不再有 payload 键。
    test_events = [e for e in events if isinstance(e, dict) and e.get('event') == 'test_completed']
    phases = [t.get('phase') for t in test_events]

    after = {'containers': codefix_containers(), 'volumes': codefix_volumes()}
    save(run_dir / 'docker-after.json', after)
    save(run_dir / 'after-ps-a.txt', '\n'.join(after['containers']) or '<none>')
    save(run_dir / 'timeline.json', timeline)

    run_path = store.directory(run_id)
    dev_results = {}
    for folder in sorted(run_path.glob('*')):
        candidate_result = folder / 'result.json'
        if candidate_result.is_file():
            dev_results[folder.name] = json.loads(candidate_result.read_text(encoding='utf-8'))
    save(run_dir / 'development-results.json', dev_results)
    save(run_dir / 'workflow-return.json',
         {k: v for k, v in box.items() if k != 'result'} | {'result': box.get('result')})

    timeout_after_stop = (min((x['offset_s'] for x in timeline if x['event'] == 'workflow_returned'),
                              default=None))
    stop_offset = at_stop.get('offset_s')
    record = {
        'mode': 'operator_stop', 'run_id': run_id,
        'final_status': final['status'], 'final_control': final['control'],
        'interrupt_at_s': stop_offset,
        'workflow_returned_at_s': timeout_after_stop,
        'seconds_from_interrupt_to_return': (round(timeout_after_stop - stop_offset, 2)
                                             if timeout_after_stop is not None and stop_offset is not None else None),
        'containers_at_stop': at_stop.get('containers', []),
        'volumes_at_stop': at_stop.get('volumes', []),
        'containers_after': after['containers'], 'volumes_after': after['volumes'],
        'test_phases': phases,
        'model_roles': model.roles,
        'workflow_return': box.get('status'), 'workflow_error': box.get('error'),
        'workflow_traceback': box.get('traceback'),
        'criteria': {
            'inflight_test_not_passed': (dev_results.get('development-1') or {}).get('status') not in (None, 'passed'),
            'inflight_test_killed': (dev_results.get('development-1') or {}).get('cleanup_passed') is True,
            'final_status_interrupted': final['status'] == 'interrupted',
            'control_is_stop': final['control'] == 'stop',
            'container_was_running_at_stop': bool(at_stop.get('containers')),
            'no_final_check_dispatch': 'final_check' not in phases,
            'no_phase_after_stop': len(phases) <= 2,
            'no_container_left': not after['containers'],
            'no_volume_left': not after['volumes'],
        },
    }
    record['criteria']['stopped_promptly'] = (record['seconds_from_interrupt_to_return'] is not None
                                              and record['seconds_from_interrupt_to_return'] <= 60)
    record['ok'] = all(v is True for k, v in record['criteria'].items())
    save(run_dir / 'stop-record.json', record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'evidence/runtime')
    parser.add_argument('--timeout-single', type=float, default=12.0)
    parser.add_argument('--timeout-split', type=float, default=60.0)
    parser.add_argument('--hard-cap', type=float, default=240.0)
    args = parser.parse_args()

    if os.environ.get('CODE_FIX_EXECUTION_APPROVED') != '1':
        raise SystemExit('Refusing to run: set CODE_FIX_EXECUTION_APPROVED=1')
    if not os.environ.get('CODE_FIX_DOCKER_IMAGE', '').startswith('sha256:'):
        raise SystemExit('Refusing to run: CODE_FIX_DOCKER_IMAGE must be a sha256 image ID')

    started = datetime.now(TZ)
    run_id = 'RUNTIME-' + started.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    run_dir = args.output_root.resolve() / run_id
    # Docker Desktop/WSL2 的 bind mount 在宿主路径过深时会直接返回 EIO
    # （实测 >210 字符时容器内 os.listdir('/w') 报 Input/output error，
    #  同一目录换到短路径即可正常挂载），因此真实运行根放在短的临时目录，
    # 跑完后把整棵证据树复制进 evidence/ 归档。
    work_root = Path(tempfile.mkdtemp(prefix='codefix-rt-'))
    before_all = {'containers': codefix_containers(), 'volumes': codefix_volumes()}
    save(work_root / 'docker-baseline.json', before_all)
    save(work_root / 'work-root.txt',
         'work_root={0}\nreason=Docker Desktop/WSL2 bind mount 深度限制（>210 字符时容器内为 EIO）\n'.format(work_root))

    records, leftovers = {}, {}
    try:
        for name, func in (('timeout-single', lambda: scenario_timeout(work_root / 'timeout-single', args.timeout_single, False)),
                           ('timeout-split', lambda: scenario_timeout(work_root / 'timeout-split', args.timeout_split, True)),
                           ('operator-stop', lambda: scenario_stop(work_root / 'operator-stop', args.hard_cap))):
            try:
                records[name] = func()
            except Exception as exc:  # noqa: BLE001
                records[name] = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc),
                                 'traceback': traceback.format_exc()[-4000:]}
            print('{0}: ok={1}'.format(name, records[name].get('ok')), flush=True)
        leftovers = {'containers': codefix_containers(), 'volumes': codefix_volumes()}
        save(work_root / 'docker-after-all.json', leftovers)
    finally:
        shutil.copytree(work_root, run_dir, dirs_exist_ok=True)
        assert work_root.name.startswith('codefix-rt-')
        assert Path(tempfile.gettempdir()).resolve() in work_root.resolve().parents
        shutil.rmtree(work_root, ignore_errors=True)
    all_ok = bool(records) and all(r.get('ok') for r in records.values()) and not leftovers.get('containers')

    summary = {
        'run_id': run_id,
        'started_at': started.isoformat(timespec='seconds'),
        'finished_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'timezone': 'Asia/Shanghai',
        'image': os.environ['CODE_FIX_DOCKER_IMAGE'],
        'python_version': sys.version,
        'docker_version': docker('version', '--format', '{{.Server.Version}}').stdout.strip(),
        'parameters': {'timeout_single': args.timeout_single, 'timeout_split': args.timeout_split,
                       'hard_cap': args.hard_cap},
        'work_root': str(work_root),
        'work_root_reason': 'Docker Desktop/WSL2 bind mount 深度限制；运行根用短临时目录，证据已复制归档',
        'scenarios': records,
        'docker_baseline': before_all,
        'leftovers_after_all': leftovers,
        'all_passed': all_ok,
    }
    save(run_dir / 'summary.json', summary)

    lines = ['# 超时与运行中停止 · 真实 Docker 实测', '',
             '运行编号：`{0}`；引擎版本：{1}；镜像：`{2}`。'.format(
                 run_id, summary['docker_version'], summary['image']), '',
             '## 场景结果', '',
             '| 场景 | 判据 | 结论 |', '| --- | --- | --- |']
    labels = {'timeout-single': 'A 超时（单容器，模块级死循环）',
              'timeout-split': 'A2 超时（双容器，函数内死循环）',
              'operator-stop': 'B 运行中停止（真实控制面中断）'}
    for key, rec in records.items():
        crit = rec.get('criteria') or {}
        detail = ', '.join('{0}={1}'.format(k, v) for k, v in crit.items()) or str(rec.get('error'))
        lines.append('| {0} | {1} | {2} |'.format(labels.get(key, key), detail,
                                                  '通过' if rec.get('ok') else '不通过'))
    lines.append('')
    for key, rec in records.items():
        lines.extend(['## ' + labels.get(key, key), ''])
        if key.startswith('timeout'):
            lines.append('- 实际耗时 {0}s（超时设定 {1}s）；采到容器：{2}'.format(
                rec.get('elapsed_seconds'), rec.get('timeout_seconds'),
                ', '.join(rec.get('containers_seen') or []) or '无'))
            res = rec.get('result_json') or {}
            lines.append('- runner 结论：status=`{0}` failure_class=`{1}` timed_out={2} cleanup_passed={3}'.format(
                res.get('status'), res.get('failure_class'), res.get('timed_out'), res.get('cleanup_passed')))
            lines.append('- 残留容器 {0} 个、残留卷 {1} 个'.format(
                len(rec.get('leftover_containers') or []), len(rec.get('leftover_volumes') or [])))
        else:
            lines.append('- 中断时刻（+{0}s）运行中的容器：{1}'.format(
                rec.get('interrupt_at_s'), ', '.join(rec.get('containers_at_stop') or []) or '无'))
            lines.append('- 中断到 workflow 返回：{0}s'.format(rec.get('seconds_from_interrupt_to_return')))
            lines.append('- 最终状态：`{0}`，control=`{1}`'.format(rec.get('final_status'), rec.get('final_control')))
            lines.append('- 实际派发的测试阶段：{0}'.format(', '.join(rec.get('test_phases') or []) or '无'))
            lines.append('- 模型角色调用序列：{0}'.format(', '.join(rec.get('model_roles') or [])))
            lines.append('- 残留容器 {0} 个、残留卷 {1} 个'.format(
                len(rec.get('containers_after') or []), len(rec.get('volumes_after') or [])))
        lines.append('')
    lines.extend(['## 结论', '',
                  ('三个场景全部通过：超时有界返回并强杀容器，运行中停止后容器与卷清理干净且无后续派发。'
                   if all_ok else '存在未通过场景，不得作为通过结论归档，需排查后再重跑。'), ''])
    (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8')

    print('Evidence: ' + str(run_dir))
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
