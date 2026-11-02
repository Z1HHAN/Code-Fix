"""Run C's reviewed controls with real pytest processes, without model calls.

These are tool/reference/fault-injection checks, not B/C integration approval
or model-repair experimental results. Requires Python 3.14.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from c_runner import compute_code_version, prepare_fixture, run_tests

TZ = timezone(timedelta(hours=8))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'evaluation/c_runs')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        raise SystemExit('This acceptance entry requires Python 3.14; no evidence run created.')
    profiles = json.loads((ROOT / 'c_runner/profiles.json').read_text(encoding='utf-8'))['profiles']
    started = datetime.now(TZ)
    run_id = 'C-' + started.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    artifacts = run_dir / 'artifacts'
    records = []
    scenarios = [
        ('leap_bad', 'leap_bad', 'failed', 'failed', 'assertion_failed', 10),
        ('leap_good', 'leap_good', 'ok', 'passed', 'passed', 10),
        ('output_timeout', 'output_timeout', 'timeout', 'inconclusive', 'timeout', 2),
        ('all_skipped', 'leap_all_skipped', None, 'inconclusive', None, 10),
        ('zero_tests', 'zero_tests', None, 'inconclusive', None, 10),
        ('incomplete_tests', 'incomplete_tests', None, 'inconclusive', None, 10),
        ('dependency_error', 'collection_dependency_error', None, 'inconclusive', 'environment_error', 10),
        ('syntax_error', 'collection_syntax_error', None, None, 'syntax_error', 10),
        ('runtime_error', 'code_runtime_error', None, None, 'code_runtime_error', 10),
        ('stale_version', 'leap_good', 'blocked', 'not_run', None, 10),
        ('modified_tests', 'leap_good', 'blocked', 'not_run', None, 10),
        ('model_candidate_blocked', 'leap_good', 'blocked', 'not_run', None, 10),
    ]
    for name, profile, wanted_status, wanted_verification, wanted_failure, timeout in scenarios:
        workspace = run_dir / 'workspaces' / name
        prepare_fixture(profile, workspace)
        version = compute_code_version(workspace, profiles[profile]['source_paths'])
        kwargs = {}
        if name == 'stale_version':
            version = 'sha256:' + '0' * 64
        elif name == 'modified_tests':
            with (workspace / 'tests/test_leap.py').open('a', encoding='utf-8') as stream:
                stream.write('\n# Deliberate C control tamper; must be blocked before execution.\n')
        elif name == 'model_candidate_blocked':
            kwargs['execution_mode'] = 'model_candidate'
        result = run_tests(run_id, 'C-control-' + name, workspace, profile, timeout,
                           version, artifacts, **kwargs)
        conditions = {
            'status': wanted_status is None or result['status'] == wanted_status,
            'verification': wanted_verification is None or result['verification_status'] == wanted_verification,
            'failure_class': wanted_failure is None or result['failure_class'] == wanted_failure,
            'no_false_pass': name == 'leap_good' or result['verification_status'] != 'passed',
            'output_agent_null': result['output_agent'] is None,
            'stdout_exists': (artifacts / result['stdout_path']).is_file(),
            'stderr_exists': (artifacts / result['stderr_path']).is_file(),
        }
        if name == 'leap_bad':
            conditions['counts'] = result['counts'] == {'collected': 4, 'passed': 3, 'failed': 1, 'errors': 0, 'skipped': 0}
        elif name == 'leap_good':
            conditions['counts'] = result['counts'] == {'collected': 4, 'passed': 4, 'failed': 0, 'errors': 0, 'skipped': 0}
        elif name == 'output_timeout':
            conditions['partial_stdout'] = b'C_TIMEOUT_STDOUT_FLUSHED' in (artifacts / result['stdout_path']).read_bytes()
            conditions['partial_stderr'] = b'C_TIMEOUT_STDERR_FLUSHED' in (artifacts / result['stderr_path']).read_bytes()
            conditions['no_fake_junit'] = result['junit_path'] is None and bool(result['junit_missing_reason'])
            conditions['unknown_outcomes'] = all(result['counts'][key] is None for key in ('passed', 'failed', 'errors', 'skipped'))
            conditions['collected_truthful'] = result['counts']['collected'] in (None, 1)
        record = {
            'scenario': name,
            'run_type': 'fault_injection' if name in {'stale_version', 'modified_tests', 'model_candidate_blocked'} else profiles[profile]['run_type'],
            'test_profile': profile,
            'expected': {'status': wanted_status, 'verification_status': wanted_verification, 'failure_class': wanted_failure},
            'checks': conditions,
            'control_check_passed': all(conditions.values()),
            'result': result,
        }
        records.append(record)
        write_json(run_dir / (name + '.record.json'), record)
        print(name + ': ' + str(record['control_check_passed']) + ' / ' + result['status'] + ' / ' + result['failure_class'])

    snapshot = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / 'c_runner').glob('*')) if p.is_file()}
    summary = {
        'run_id': run_id,
        'started_at': started.isoformat(timespec='seconds'),
        'completed_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'timezone': 'Asia/Shanghai',
        'interface_version': '0.2-20261101-draft',
        'run_type': 'tool_check',
        'scope': 'C reviewed fixtures and fault injection only',
        'python_version': sys.version,
        'python_executable': sys.executable,
        'pytest_version': importlib.metadata.version('pytest'),
        'runner_files_sha256': snapshot,
        'source_commit': None,
        'source_commit_reason': 'Workspace derived from user archive; no new Git commit created by this entry.',
        'model_api_called': False,
        'isolation_ready': False,
        'isolation_status': 'not_verified; host execution limited to exact approved fixture snapshots',
        'bc_integration': 'not_run; C producer self-check only',
        'interface_freeze': 'pending_BC_confirmation',
        'experiment_result': 'not_applicable; not model_repair',
        'artifact_paths_relative_to': 'this run directory / artifacts',
        'all_control_checks_passed': all(r['control_check_passed'] for r in records),
        'scenario_count': len(records),
        'records': records,
    }
    write_json(run_dir / 'summary.json', summary)
    lines = ['# C 受控执行器检查', '', f'运行编号：`{run_id}`。', '',
             '这些是真实工具/参考/故障注入检查；未调用模型，不计模型修复成绩。接口仍待 B/C 确认，隔离未验证。', '',
             '| 检查 | 工具状态 | 验证结论 | 分类 | 本项控制检查 |', '| --- | --- | --- | --- | --- |']
    for r in records:
        t = r['result']
        lines.append(f"| {r['scenario']} | {t['status']} | {t['verification_status']} | {t['failure_class']} | {'通过' if r['control_check_passed'] else '未通过'} |")
    lines.extend(['', '原始日志、JUnit、结构化记录及清理信息见各项 record.json 指向的 artifacts 路径。',
                  'source_commit 为空，仅用源码文件哈希绑定本次实现；不得补写不存在的 Git 提交。', ''])
    (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print('Evidence: ' + str(run_dir))
    return 0 if summary['all_control_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
