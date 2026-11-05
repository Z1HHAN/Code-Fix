"""四道题的原始坏版本在真实 Docker 内的基线实测。

目的：证明 baseline 阶段的失败来自被测代码自身的断言失败/运行时错误，
而不是镜像、挂载、RPC、超时或清理导致的环境错误。

- 被测 runner：app.split_runner.SplitDockerRunner（两容器信任边界）
- 被测代码：task_assets 中的原始（未修复）版本，不注入任何补丁
- 判据：status=failed 且 verification_status=failed 且
        failure_class ∈ {assertion_failed, code_runtime_error}，
        且 JUnit 中 failures+errors > 0，且未超时、清理通过。

用法：
    set CODE_FIX_EXECUTION_APPROVED=1
    set CODE_FIX_DOCKER_IMAGE=sha256:<镜像ID>
    python scripts/docker_baselines.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.runner import code_version  # noqa: E402
from app.split_runner import SplitDockerRunner  # noqa: E402
from app.tasks_v3 import get_task  # noqa: E402

TZ = timezone(timedelta(hours=8))
PROJECTS = ('leap', 'qb-get_factors', 'qb-gcd', 'qb-sqrt')
EXPECTED_CLASSES = ('assertion_failed', 'code_runtime_error')


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=120)


def leftovers():
    containers = docker('ps', '-a', '--filter', 'name=codefix-', '--format', '{{.Names}}|{{.Status}}')
    volumes = docker('volume', 'ls', '--filter', 'name=codefix-rpc-', '--format', '{{.Name}}')
    return {
        'containers': [x for x in containers.stdout.splitlines() if x.strip()],
        'volumes': [x for x in volumes.stdout.splitlines() if x.strip()],
    }


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def junit_stats(path: Path) -> dict:
    stats = {'present': path.is_file(), 'tests': 0, 'failures': 0, 'errors': 0,
             'skipped': 0, 'failed_cases': []}
    if not path.is_file():
        return stats
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        stats['parse_error'] = type(exc).__name__
        return stats
    root = tree.getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    for suite in suites:
        for key in ('tests', 'failures', 'errors', 'skipped'):
            try:
                stats[key] += int(suite.get(key) or 0)
            except (TypeError, ValueError):
                pass
    for case in root.iter('testcase'):
        for kind in ('failure', 'error'):
            node = case.find(kind)
            if node is not None:
                message = (node.get('message') or node.text or '').strip().splitlines()
                stats['failed_cases'].append({
                    'nodeid': '{}::{}'.format(case.get('classname') or '', case.get('name') or ''),
                    'kind': kind,
                    'message': message[0][:400] if message else '',
                })
                break
    return stats


def attribute_failure(run_folder: Path, stats: dict) -> str:
    """把 JUnit 现象归因到被测代码，避免把 RPC 超时误读成环境故障。"""
    stderr_path = run_folder / 'candidate-stderr.txt'
    stderr = stderr_path.read_text(encoding='utf-8', errors='replace') if stderr_path.is_file() else ''
    if 'RecursionError' in stderr:
        return '被测函数递归不收敛，候选进程抛出 RecursionError'
    messages = ' '.join(case.get('message', '') for case in stats.get('failed_cases', []))
    if 'TimeoutError' in messages or 'BlockingIOError' in messages:
        return '被测函数不返回（死循环），单次 RPC 调用超时；runner 整体未超时且清理通过'
    if stats.get('failures') or stats.get('errors'):
        return '断言结果与预期不符'
    return '未从 JUnit 中识别到失败原因'


def run_one(project: str, run_dir: Path, timeout: float) -> dict:
    task = get_task(project)
    workspace = run_dir / 'workspaces' / project
    task.prepare(workspace)
    source = workspace / task.source
    source_sha = sha256_file(source)
    version = code_version(workspace, (task.source,))

    snapshot = run_dir / project / 'source-snapshot.py'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, snapshot)

    runner = SplitDockerRunner(project)
    isolation = runner.preflight(run_dir / project / 'isolation')
    record = {
        'project': project,
        'task_source': task.source,
        'source_sha256': source_sha,
        'code_version': version,
        'expected_test_count': len(task.profile['expected_nodeids']),
        'timeout_seconds': timeout,
        'isolation_ready': isolation.get('isolation_ready'),
        'isolation_reason': isolation.get('reason'),
        'source_snapshot': str(snapshot.relative_to(run_dir)),
    }
    started = datetime.now(TZ)
    if not isolation.get('isolation_ready'):
        record.update(status='blocked', verification_status='not_run',
                      failure_class='environment_error', reason='isolation preflight failed')
    else:
        try:
            result = runner.run(workspace, run_dir / project / 'run', phase='baseline',
                                timeout=timeout, expected_version=version)
            record.update(result)
        except Exception as exc:  # noqa: BLE001 - 任何异常都必须落成 environment_error
            record.update(status='blocked', verification_status='not_run',
                          failure_class='environment_error',
                          error=type(exc).__name__ + ': ' + str(exc))

    run_folder = run_dir / project / 'run'
    stats = junit_stats(run_folder / 'trusted-run' / 'output' / 'junit.xml')
    record['junit'] = stats
    record['attribution'] = attribute_failure(run_folder, stats)
    record['finished_at'] = datetime.now(TZ).isoformat(timespec='milliseconds')
    record['duration_seconds'] = round((datetime.now(TZ) - started).total_seconds(), 2)
    record['baseline_ok'] = bool(
        record.get('status') == 'failed'
        and record.get('verification_status') == 'failed'
        and record.get('failure_class') in EXPECTED_CLASSES
        and record.get('cleanup_passed') is True
        and record.get('timed_out') is not True
        and stats['present']
        and (stats['failures'] + stats['errors']) > 0
    )
    save(run_dir / project / 'baseline-record.json', record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'evidence/baseline')
    parser.add_argument('--timeout', type=float, default=90.0)
    args = parser.parse_args()

    if os.environ.get('CODE_FIX_EXECUTION_APPROVED') != '1':
        raise SystemExit('Refusing to run: set CODE_FIX_EXECUTION_APPROVED=1')
    if not os.environ.get('CODE_FIX_DOCKER_IMAGE', '').startswith('sha256:'):
        raise SystemExit('Refusing to run: CODE_FIX_DOCKER_IMAGE must be a sha256 image ID')

    started = datetime.now(TZ)
    run_id = 'BASELINE-' + started.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    records = []
    for project in PROJECTS:
        record = run_one(project, run_dir, args.timeout)
        records.append(record)
        print('{0}: baseline_ok={1} status={2} verification={3} failure_class={4} exit={5} tests={6} failures={7} errors={8}'.format(
            project, record['baseline_ok'], record.get('status'), record.get('verification_status'),
            record.get('failure_class'), record.get('exit_code'),
            record['junit']['tests'], record['junit']['failures'], record['junit']['errors']), flush=True)

    all_ok = all(r['baseline_ok'] for r in records)
    summary = {
        'run_id': run_id,
        'started_at': started.isoformat(timespec='seconds'),
        'finished_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'timezone': 'Asia/Shanghai',
        'runner': 'app.split_runner.SplitDockerRunner',
        'image': os.environ['CODE_FIX_DOCKER_IMAGE'],
        'python_version': sys.version,
        'projects': list(PROJECTS),
        'timeout_seconds': args.timeout,
        'all_baseline_failed_as_expected': all_ok,
        'records': records,
        'leftovers': leftovers(),
    }
    save(run_dir / 'summary.json', summary)

    lines = [
        '# 四道题原始坏版本 · 容器内基线实测', '',
        '运行编号：`{0}`；runner：`SplitDockerRunner`；镜像：`{1}`；超时：{2:g}s。'.format(
            run_id, summary['image'], args.timeout), '',
        '| 题目 | 源文件 | 源 SHA256（前 12 位） | 容器退出码 | tests | failures | errors | failure_class | 结论 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |',
    ]
    for r in records:
        lines.append('| {0} | `{1}` | `{2}` | {3} | {4} | {5} | {6} | `{7}` | {8} |'.format(
            r['project'], r['task_source'], r['source_sha256'][:12], r.get('exit_code'),
            r['junit']['tests'], r['junit']['failures'], r['junit']['errors'],
            r.get('failure_class'), '符合预期（真实失败）' if r['baseline_ok'] else '不符合预期'))
    lines.extend(['', '## 失败原因摘录', ''])
    for r in records:
        lines.append('### ' + r['project'])
        lines.append('')
        if r.get('reason'):
            lines.append('- runner 结论：' + str(r['reason']))
        if r.get('attribution'):
            lines.append('- 失败归因：' + str(r['attribution']))
        if r['junit']['failed_cases']:
            for case in r['junit']['failed_cases'][:3]:
                lines.append('- `{0}`：{1}'.format(case['nodeid'], case['message']))
        else:
            lines.append('- JUnit 中未记录失败用例（需人工复核）')
        lines.append('')
    verdict = ('四道题均在真实 Docker 中按预期失败，失败来自被测代码断言/运行时错误，'
               '无超时与环境错误。' if all_ok else '存在不符合预期的基线结果，需先排查，不得作为基线归档。')
    lines.extend(['## 结论', '', verdict, ''])
    (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8')

    print('Evidence: ' + str(run_dir))
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())