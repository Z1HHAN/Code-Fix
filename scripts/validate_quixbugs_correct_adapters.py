"""Validate the reviewed SplitDockerRunner RPC adapters with upstream correct code.

This script never calls a model and never reads API keys.  It verifies the
official QuixBugs correct files against a pinned local Git object database,
then executes each registered pure-JSON task through the existing two-container
RPC path in Docker.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = '4257f44b0ff1181dedaedee6a447e133219fcebf'
UPSTREAM_DIR = ROOT / 'runs/quixbugs-upstream-4257f44b'
BUGGY_PREFIX = 'python_programs'

sys.path.insert(0, str(ROOT))

from app.runner import code_version  # noqa: E402
from app.split_runner import FUNCTIONS, MATERIALIZE_AS_LIST, SplitDockerRunner  # noqa: E402
from app.tasks_v3 import get_task  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def run_git(upstream: Path, *args: str) -> bytes:
    command = ['git', '-c', f'safe.directory={upstream}', '-C', str(upstream), *args]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f'git command failed ({completed.returncode}): {" ".join(args)}: '
            + completed.stderr.decode('utf-8', 'replace')[:500]
        )
    return completed.stdout


def verify_pinned_checkout(upstream: Path) -> None:
    head = run_git(upstream, 'rev-parse', 'HEAD').decode().strip()
    if head != PINNED_COMMIT:
        raise ValueError(f'upstream checkout is {head}, expected {PINNED_COMMIT}')
    dirty = run_git(upstream, 'status', '--porcelain')
    if dirty.strip():
        raise ValueError('upstream checkout is not clean')


def load_tree_blobs(upstream: Path, prefix: str) -> dict[str, dict]:
    tree = run_git(upstream, 'ls-tree', '-r', PINNED_COMMIT, '--', prefix)
    blobs = {}
    for line in tree.decode('utf-8').splitlines():
        metadata, path = line.split('\t', 1)
        mode, kind, object_id = metadata.split()
        if kind != 'blob' or not path.endswith('.py'):
            continue
        blobs[path] = {'mode': mode, 'git_blob_sha': object_id}
    if not blobs:
        raise ValueError(f'no official {prefix} blobs found')
    return blobs


def load_correct_blobs(upstream: Path) -> dict[str, dict]:
    verify_pinned_checkout(upstream)
    return load_tree_blobs(upstream, 'correct_python_programs')


def select_tasks() -> list[tuple[str, object, str]]:
    selected = []
    for project in sorted(FUNCTIONS):
        if not project.startswith('qb-'):
            continue
        task = get_task(project)
        name = Path(task.source).stem
        selected.append((project, task, name))
    return selected


def read_struct_skips(task_dir: Path, result: dict) -> list[str]:
    relative = result.get('structured_path')
    if not relative:
        return []
    path = task_dir / relative
    try:
        evidence = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return sorted({row.get('nodeid') for row in evidence.get('reports', [])
                   if row.get('outcome') == 'skipped' and isinstance(row.get('nodeid'), str)})


def contract_ok(result: dict, expected_count: int) -> bool:
    counts = result.get('counts') or {}
    audit = result.get('candidate_audit') or {}
    return bool(
        result.get('status') == 'ok'
        and result.get('verification_status') == 'passed'
        and result.get('failure_class') == 'passed'
        and result.get('isolation_ready') is True
        and result.get('cleanup_passed') is True
        and result.get('artifact_channel') == 'docker-cp'
        and result.get('trust_boundary') == 'separate_candidate_and_test_containers'
        and audit.get('logs_captured') is True
        and audit.get('violation') is None
        and counts.get('collected') == expected_count
        and counts.get('failed') == 0
        and counts.get('errors') == 0
        and (counts.get('passed') or 0) + (counts.get('skipped') or 0) == expected_count
        and (counts.get('passed') or 0) > 0
    )


def summarize_result(result: dict) -> dict:
    audit = result.get('candidate_audit') or {}
    return {
        'status': result.get('status'),
        'verification_status': result.get('verification_status'),
        'failure_class': result.get('failure_class'),
        'reason': result.get('reason'),
        'exit_code': result.get('exit_code'),
        'counts': result.get('counts'),
        'expected_test_count': result.get('expected_test_count'),
        'isolation_ready': result.get('isolation_ready'),
        'cleanup_passed': result.get('cleanup_passed'),
        'artifact_channel': result.get('artifact_channel'),
        'trust_boundary': result.get('trust_boundary'),
        'evidence_matches_current_version': result.get('evidence_matches_current_version'),
        'candidate_audit': {
            'logs_captured': audit.get('logs_captured'),
            'event_kinds': audit.get('event_kinds'),
            'fake_report_output': audit.get('fake_report_output'),
            'violation': audit.get('violation'),
            'reason': audit.get('reason'),
        },
    }


def negative_control_ok(result: dict) -> bool:
    """The pinned buggy source must be rejected overall.

    QuixBugs bugs are input-specific: a buggy file may still pass individual
    cases.  The gate is the runner's failed verdict plus a recorded failing
    case, never a requirement that every single case fails.
    """
    counts = result.get('counts') or {}
    return bool(
        result.get('status') == 'failed'
        and result.get('verification_status') == 'failed'
        and result.get('failure_class') in {'assertion_failed', 'code_runtime_error'}
        and counts.get('failed', 0) > 0
    )


def apply_in_memory_skips(runner: SplitDockerRunner, skips: dict[str, str]) -> None:
    """Apply an exact native-skip policy to the runner's own reloaded task.

    SplitDockerRunner constructs its task from the registry, so mutating a
    different get_task() instance would silently leave the production
    admission policy in force.
    """
    runner.task.data['profile']['allowed_skips'] = deepcopy(skips)
    if runner.task.profile.get('allowed_skips') != skips:
        raise RuntimeError(f'in-memory allowed_skips was not applied: {runner.task.project}')


def execute_task(runner: SplitDockerRunner, task, task_dir: Path, work: Path,
                 timeout: int) -> dict:
    version = code_version(work, task.profile['source_paths'])
    started = time.monotonic()
    result = runner.run(work, task_dir, phase='development', timeout=timeout,
                        expected_version=version)
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', type=Path, default=UPSTREAM_DIR)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--batch', default=time.strftime('batch-%Y%m%d-%H%M%S'))
    parser.add_argument('--report-dir', type=Path,
                        default=ROOT / 'evidence/quixbugs-adapter-validation')
    args = parser.parse_args()

    if os.environ.get('CODE_FIX_EXECUTION_APPROVED') != '1':
        raise SystemExit('CODE_FIX_EXECUTION_APPROVED=1 is required')
    image = os.environ.get('CODE_FIX_DOCKER_IMAGE', '')
    if not image:
        raise SystemExit('CODE_FIX_DOCKER_IMAGE is required')

    upstream = args.upstream.resolve()
    if not (upstream / '.git').exists():
        raise SystemExit(f'pinned upstream checkout not found: {upstream}')
    blobs = load_correct_blobs(upstream)
    buggy_blobs = load_tree_blobs(upstream, BUGGY_PREFIX)
    selected = select_tasks()
    missing = [f'correct_python_programs/{name}.py' for _, _, name in selected
               if f'correct_python_programs/{name}.py' not in blobs]
    if missing:
        raise SystemExit('missing official correct files: ' + ', '.join(missing))

    run_root = ROOT / 'runs/quixbugs-adapter-validation' / args.batch
    if run_root.exists():
        raise SystemExit(f'run directory already exists: {run_root}')
    (run_root / 'workspaces').mkdir(parents=True)
    (run_root / 'tasks').mkdir()
    (run_root / 'negative-workspaces').mkdir()
    (run_root / 'negative-tasks').mkdir()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    probe_runner = SplitDockerRunner('qb-bitcount')
    preflight = probe_runner.preflight(run_root / 'preflight')
    if not preflight.get('isolation_ready'):
        raise SystemExit('isolation preflight failed: ' + json.dumps(preflight, ensure_ascii=False))
    executable = probe_runner.executable
    if not executable:
        raise SystemExit('docker executable not found')

    print(json.dumps({
        'event': 'start', 'batch': args.batch, 'tasks': len(selected),
        'upstream_commit': PINNED_COMMIT, 'model_api_called': False,
        'image': image,
    }, ensure_ascii=False), flush=True)

    records = []
    failures = 0
    for index, (project, task, name) in enumerate(selected, 1):
        blob_path = f'correct_python_programs/{name}.py'
        blob_id = blobs[blob_path]['git_blob_sha']
        correct = run_git(upstream, 'cat-file', 'blob', blob_id)
        if git_blob_sha(correct) != blob_id:
            raise RuntimeError(f'official blob hash mismatch: {blob_path}')

        work = task.prepare(run_root / 'workspaces' / project)
        target = work / task.source
        target.write_bytes(correct)
        if sha256(target.read_bytes()) != sha256(correct):
            raise RuntimeError(f'correct source was not installed: {project}')
        task.verify(work, candidate=True)

        runner = SplitDockerRunner(project)
        runner.ready = True
        runner.executable = executable
        runner.image = image
        task_dir = run_root / 'tasks' / project
        record = {
            'index': index, 'project': project, 'function': FUNCTIONS[project],
            'source': task.source, 'correct_blob_path': blob_path,
            'correct_git_blob_sha': blob_id, 'correct_sha256': sha256(correct),
            'expected_test_count': len(task.profile['expected_nodeids']),
            'production_profile': {'allowed_skips': deepcopy(task.profile.get('allowed_skips', {}))},
        }

        try:
            result = execute_task(runner, task, task_dir, work, args.timeout)
            record['production_run'] = summarize_result(result)
            record['production_passed'] = contract_ok(result, record['expected_test_count'])
            skipped = read_struct_skips(task_dir, result)
            record['skipped_nodeids'] = skipped
            if record['production_passed']:
                record['adapter_contract_passed'] = True
                record['validation_profile'] = 'production'
            elif skipped and set(skipped).issubset(set(task.profile['expected_nodeids'])):
                # The upstream native test itself skips these exact cases.  A
                # second in-memory-only run proves the RPC adapter is sound
                # without changing the repository's admission policy.
                exact_skips = {node: 'official upstream native test skip' for node in skipped}
                task.data['profile']['allowed_skips'] = deepcopy(exact_skips)
                validation_dir = run_root / 'tasks' / (project + '-native-skip-validation')
                validation_runner = SplitDockerRunner(project)
                validation_runner.ready = True
                validation_runner.executable = executable
                validation_runner.image = image
                # SplitDockerRunner re-loads the task from the registry, so the
                # in-memory policy must be applied to the runner's own task.
                apply_in_memory_skips(validation_runner, exact_skips)
                rerun = execute_task(validation_runner, validation_runner.task, validation_dir,
                                     work, args.timeout)
                record['validation_profile'] = {
                    'mode': 'in_memory_exact_native_skips',
                    'allowed_skips': deepcopy(validation_runner.task.profile['allowed_skips']),
                }
                record['validation_run'] = summarize_result(rerun)
                record['adapter_contract_passed'] = contract_ok(
                    rerun, record['expected_test_count'])
            else:
                record['validation_profile'] = 'not_needed'
                record['adapter_contract_passed'] = False
        except Exception as error:  # Keep a per-task record and continue the batch.
            record['error'] = type(error).__name__ + ': ' + str(error)
            record['traceback'] = traceback.format_exc(limit=4)
            record['adapter_contract_passed'] = False

        if not record['adapter_contract_passed']:
            failures += 1
        records.append(record)
        print(json.dumps({
            'event': 'task', 'index': index, 'total': len(selected), 'project': project,
            'production_passed': record.get('production_passed'),
            'adapter_contract_passed': record['adapter_contract_passed'],
            'validation_profile': record.get('validation_profile'),
            'counts': (record.get('validation_run') or record.get('production_run') or {}).get('counts'),
            'skipped_nodeids': record.get('skipped_nodeids'),
            'error': record.get('error'),
        }, ensure_ascii=False), flush=True)


    # Negative controls: the same adapter must reject the pinned buggy source.
    # Without this, a "correct passes" result could hide an adapter that passes
    # everything.  Only the generator tasks exercise the MATERIALIZE_AS_LIST
    # path; the other adapters have no return transform to regress.
    negative_controls = []
    for name in sorted(MATERIALIZE_AS_LIST):
        matches = [(project, task) for project, task, selected_name in selected
                   if selected_name == name]
        if not matches:
            continue
        project, task = matches[0]
        blob_path = f'{BUGGY_PREFIX}/{name}.py'
        blob_id = buggy_blobs[blob_path]['git_blob_sha']
        buggy = run_git(upstream, 'cat-file', 'blob', blob_id)
        if git_blob_sha(buggy) != blob_id:
            raise RuntimeError(f'official blob hash mismatch: {blob_path}')
        work = task.prepare(run_root / 'negative-workspaces' / project)
        (work / task.source).write_bytes(buggy)
        task.verify(work, candidate=True)
        runner = SplitDockerRunner(project)
        runner.ready = True
        runner.executable = executable
        runner.image = image
        result = execute_task(runner, task, run_root / 'negative-tasks' / project,
                              work, args.timeout)
        record = {
            'project': project, 'function': name,
            'buggy_blob_path': blob_path, 'buggy_git_blob_sha': blob_id,
            'buggy_sha256': sha256(buggy),
            'result': summarize_result(result),
            'failed_as_expected': negative_control_ok(result),
        }
        negative_controls.append(record)
        print(json.dumps({
            'event': 'negative_control', 'project': project,
            'buggy_git_blob_sha': blob_id,
            'failure_class': record['result'].get('failure_class'),
            'failed_as_expected': record['failed_as_expected'],
        }, ensure_ascii=False), flush=True)
    negative_failures = sum(not record['failed_as_expected'] for record in negative_controls)

    production_passed = sum(bool(r.get('production_passed')) for r in records)
    native_skip_validated = sum(
        r.get('validation_profile', {}).get('mode') == 'in_memory_exact_native_skips'
        if isinstance(r.get('validation_profile'), dict) else False
        for r in records
    )
    summary = {
        'schema_version': 1,
        'batch': args.batch,
        'upstream_repository': 'https://github.com/jkoppel/QuixBugs',
        'upstream_commit': PINNED_COMMIT,
        'correct_source_verification': 'pinned commit + git ls-tree blob SHA + cat-file bytes',
        'model_api_called': False,
        'api_cost_cny': 0,
        'docker_image': image,
        'preflight': {k: preflight.get(k) for k in
                      ('isolation_ready', 'image', 'reason', 'engine_id', 'scope')},
        'task_count': len(records),
        'production_profile_passed': production_passed,
        'native_skip_in_memory_validated': native_skip_validated,
        'adapter_contract_passed': len(records) - failures,
        'adapter_contract_failed': failures,
        'all_adapter_contracts_passed': failures == 0,
        'production_profile_all_passed': production_passed == len(records),
        'run_root': str(run_root.relative_to(ROOT)).replace('\\', '/'),
        'negative_controls': negative_controls,
        'negative_controls_all_failed_as_expected': negative_failures == 0,
        'tasks': records,
    }
    batch_dir = args.report_dir / 'batches' / args.batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    result_text = json.dumps(summary, ensure_ascii=False, indent=2) + '\n'
    result_path = args.report_dir / 'results.json'
    result_path.write_text(result_text, encoding='utf-8')
    (batch_dir / 'results.json').write_text(result_text, encoding='utf-8')

    lines = [
        '# QuixBugs correct 版本 · RPC 适配器加固验证',
        '',
        f'- 批次：`{args.batch}`',
        f'- 上游：`https://github.com/jkoppel/QuixBugs`',
        f'- 固定提交：`{PINNED_COMMIT}`',
        f'- 验证方式：固定 Git 对象 SHA + `git cat-file` 原始字节 + 现有双容器 RPC 路径',
        f'- 模型调用：`false`；API 费用：`0 CNY`',
        f'- Docker 镜像：`{image}`',
        f'- 任务数：`{len(records)}`',
        f'- 生产 profile 直接通过：`{production_passed}/{len(records)}`',
        f'- 精确原生 skip 策略验证通过：`{native_skip_validated}`',
        f'- RPC 适配器契约通过：`{len(records) - failures}/{len(records)}`',
        '',
        '| 题目 | 生产 profile | 验证方式 | passed | skipped | 适配器结论 |',
        '|---|---|---|---:|---:|---|',
    ]
    for record in records:
        profile_mode = record.get('validation_profile')
        if isinstance(profile_mode, dict):
            mode = '精确原生 skip（内存策略）'
        else:
            mode = str(profile_mode)
        shown = record.get('validation_run') or record.get('production_run') or {}
        counts = shown.get('counts') or {}
        production = '通过' if record.get('production_passed') else '阻断/需策略'
        conclusion = '通过' if record.get('adapter_contract_passed') else '失败'
        lines.append(
            f"| `{record['project']}` | {production} | {mode} | "
            f"{counts.get('passed', '-')} | {counts.get('skipped', '-')} | {conclusion} |"
        )
    if failures:
        lines += ['', '## 失败项', '']
        for record in records:
            if not record.get('adapter_contract_passed'):
                lines.append(f"- `{record['project']}`：{record.get('error') or (record.get('production_run') or {}).get('reason')}")
    lines += [
        '',
        '> 说明：`qb-knapsack` 与 `qb-levenshtein` 的官方原生测试各含一个明确的慢测 `pytest.skip`。',
        '> 精确 nodeid 已按用户批准（2026-11-06）登记在生产 profile 的 allowed_skips 中；',
        '> 未登记的 skip 仍会被准入策略阻断，反向对照仍要求整体判定失败。',
        '',
    ]
    if negative_controls:
        lines += [
            '## 反向对照（官方坏版本必须失败）',
            '',
            '| 题目 | 坏版本 blob | failure_class | passed | 结论 |',
            '|---|---|---|---:|---|',
        ]
        for record in negative_controls:
            shown = record['result']
            counts = shown.get('counts') or {}
            lines.append(
                f"| `{record['project']}` | `{record['buggy_git_blob_sha'][:12]}` | "
                f"{shown.get('failure_class')} | {counts.get('passed', '-')} | "
                f"{'按预期失败' if record['failed_as_expected'] else '异常通过'} |"
            )
        lines += ['', '> 反向对照证明 `MATERIALIZE_AS_LIST` 只是照搬原生 `list(...)` 语义，'
                      '不会把坏版本判成通过。', '']
    summary_text = '\n'.join(lines)
    (args.report_dir / 'SUMMARY.md').write_text(summary_text, encoding='utf-8')
    (batch_dir / 'SUMMARY.md').write_text(summary_text, encoding='utf-8')
    print(json.dumps({
        'event': 'done', 'batch': args.batch,
        'production_profile_passed': production_passed,
        'native_skip_in_memory_validated': native_skip_validated,
        'adapter_contract_passed': len(records) - failures,
        'adapter_contract_failed': failures,
        'all_adapter_contracts_passed': failures == 0,
        'negative_controls': len(negative_controls),
        'negative_controls_all_failed_as_expected': negative_failures == 0,
        'results': str(result_path.relative_to(ROOT)).replace('\\', '/'),
        'summary': str((args.report_dir / 'SUMMARY.md').relative_to(ROOT)).replace('\\', '/'),
        'batch_results': str((batch_dir / 'results.json').relative_to(ROOT)).replace('\\', '/'),
    }, ensure_ascii=False), flush=True)
    return 0 if failures == 0 and negative_failures == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
