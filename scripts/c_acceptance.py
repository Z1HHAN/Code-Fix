"""C-side offline acceptance for an explicit repository version.

This checks reviewed platform tests and independent repair gates. It never
enables live models, Docker qualification, or arbitrary candidate execution.
Exit 0: every required check passed; 1: defects detected; 2: environment or
evidence incomplete. Original stdout/stderr/JUnit and failed checks are kept.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile
import xml.etree.ElementTree as ET

TZ = timezone(timedelta(hours=8))
SOURCE_DIRS = ('app', 'c_runner', 'docker', 'configs', 'prompts', 'tests',
               'c_tests', 'acceptance_tests', 'scripts', 'c_fixtures', 'ui',
               'task_assets', 'knowledge')
SOURCE_FILES = ('requirements.txt', 'requirements-lock.txt', 'pytest.ini',
                'run_C_acceptance.cmd', 'AGENTS.md')
STAGES = ('doctor', 'validate_config', 'platform_pytest', 'node_ui',
          'c_controls', 'repair_gates')


def now():
    return datetime.now(TZ).isoformat(timespec='milliseconds')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(repo):
    paths = [repo / name for name in SOURCE_FILES if (repo / name).is_file()]
    for directory in SOURCE_DIRS:
        paths.extend(p for p in (repo / directory).rglob('*') if p.is_file()
                     and not any(x in p.parts for x in ('__pycache__', '.pytest_cache'))
                     and p.suffix not in ('.pyc', '.pyo'))
    return {p.relative_to(repo).as_posix():
            ('symlink:' + str(p.readlink()) if p.is_symlink() else digest(p))
            for p in sorted(set(paths))}


def preserve_work(source, destination):
    """Copy regular work artifacts for sharing, never follow test-created links."""
    omitted = []
    if not source.exists():
        return {'source': str(source), 'copied': False, 'omitted_links': omitted}
    # A ZIP retains relative artifact names without recreating Windows MAX_PATH
    # paths in the evidence tree. Keep the original short workspace as well.
    archive = destination.with_suffix('.zip')
    hashes = {}
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for base, directories, files in os.walk(source, followlinks=False):
            base = Path(base)
            for name in list(directories):
                path = base / name
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    directories.remove(name)
                    omitted.append(path.relative_to(source).as_posix())
            for name in files:
                path = base / name
                relative = path.relative_to(source).as_posix()
                if path.is_symlink():
                    omitted.append(relative)
                else:
                    raw = path.read_bytes()
                    bundle.writestr(relative, raw)
                    hashes[relative] = hashlib.sha256(raw).hexdigest()
    return {'source': str(source), 'copied': True, 'format': 'zip',
            'archive': archive.name, 'archive_sha256': digest(archive),
            'file_sha256': hashes, 'omitted_links': omitted, 'original_retained': True}


def environment(folder):
    # A minimal OS/tool-search environment, with no inherited model credentials,
    # paid-run approvals, docker execution approval, or Python/pytest overrides.
    allow = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PATHEXT', 'PATH'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allow}
    temp = folder / 'temp'
    temp.mkdir()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1',
               TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
               CODE_FIX_RUN_ROOT=str(folder / 'service-runs'))
    return env


def check_environment(repo):
    lock = repo / 'requirements-lock.txt'
    checks = {'python_required': '3.14', 'python_actual': sys.version,
              'python_matches': sys.version_info[:2] == (3, 14),
              'dependencies': [], 'issues': []}
    if not checks['python_matches']:
        checks['issues'].append('Use Python 3.14 for this acceptance run.')
    if not lock.is_file():
        checks['issues'].append('requirements-lock.txt is missing')
        return checks
    for line in lock.read_text(encoding='utf-8-sig').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', line)
        if not match:
            checks['issues'].append('Unsupported lock entry: ' + line)
            continue
        package, expected = match.groups()
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        checks['dependencies'].append({'package': package, 'expected': expected,
                                       'actual': actual, 'matches': actual == expected})
        if actual != expected:
            checks['issues'].append(f'{package}: expected {expected}, found {actual}')
    return checks


def repository_identity(repo, provenance):
    result = {'git_commit': None, 'git_branch': None, 'git_status': None,
              'git_identity_verified': False, 'archive_provenance': None}
    if provenance:
        try:
            result['archive_provenance'] = json.loads(provenance.read_text(encoding='utf-8-sig'))
            result['archive_provenance_file_sha256'] = digest(provenance)
        except (OSError, ValueError) as exc:
            result['archive_provenance_error'] = str(exc)
    if not (repo / '.git').exists():
        result['git_reason'] = 'No .git in the selected root; outer workspace HEAD is deliberately ignored.'
        return result
    git = shutil.which('git')
    if not git:
        result['git_reason'] = 'Git executable unavailable'
        return result
    try:
        def read(*args):
            p = subprocess.run([git, '-C', str(repo), *args], capture_output=True,
                               timeout=15, check=True)
            return p.stdout.decode('utf-8', errors='replace').strip()
        actual_root = Path(read('rev-parse', '--show-toplevel')).resolve()
        if actual_root != repo:
            raise ValueError('Git top-level is not the selected repository root')
        result.update(git_commit=read('rev-parse', 'HEAD'), git_branch=read('branch', '--show-current'),
                      git_status=read('status', '--short', '--untracked-files=normal'),
                      git_identity_verified=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result['git_reason'] = str(exc)
    return result


def junit_counts(path):
    root = ET.parse(path).getroot()
    if root.tag not in ('testsuite', 'testsuites'):
        raise ValueError('Unexpected JUnit root')
    cases = root.findall('.//testcase')
    if not cases:
        raise ValueError('JUnit contains no testcases')
    counts = {'collected': len(cases), 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
    skipped = []
    failed = []
    for case in cases:
        name = case.get('classname', '') + '::' + case.get('name', '')
        error = case.find('error')
        failure = case.find('failure')
        skip = case.find('skipped')
        if error is not None:
            counts['errors'] += 1
        if failure is not None:
            counts['failed'] += 1
        if skip is not None:
            counts['skipped'] += 1
            skipped.append({'test': name, 'message': skip.get('message', ''),
                            'type': skip.get('type', ''), 'details': skip.text or ''})
        if error is None and failure is None and skip is None:
            counts['passed'] += 1
        if error is not None or failure is not None:
            failed.append({'test': name, 'kind': 'error' if error is not None else 'failure',
                           'message': (error if error is not None else failure).get('message', '')})
    return counts, skipped, failed


def execute(name, argv, repo, run_dir, env, timeout):
    folder = run_dir / name
    folder.mkdir()
    record = {'stage': name, 'argv': argv, 'cwd': str(repo), 'started_at': now(),
              'timeout_seconds': timeout, 'exit_code': None, 'timed_out': False,
              'status': 'blocked', 'reason': None,
              'stdout_path': f'{name}/stdout.txt', 'stderr_path': f'{name}/stderr.txt',
              'cleanup': {'parent_reaped': False, 'tree_termination_attempted': False,
                          'full_process_tree_verified': False}}
    started = time.monotonic()
    process = None
    with (folder / 'stdout.txt').open('wb') as stdout, (folder / 'stderr.txt').open('wb') as stderr:
        try:
            options = {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen(argv, cwd=repo, env=env, stdout=stdout, stderr=stderr, **options)
            record['pid'] = process.pid
            try:
                record['exit_code'] = process.wait(timeout=timeout)
                record['cleanup']['parent_reaped'] = True
            except subprocess.TimeoutExpired:
                record['timed_out'] = True
                record['cleanup']['tree_termination_attempted'] = True
                if os.name == 'nt':
                    killer = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32/taskkill.exe'
                    killed = subprocess.run([str(killer), '/PID', str(process.pid), '/T', '/F'],
                                            capture_output=True, timeout=10)
                    record['cleanup']['termination_exit_code'] = killed.returncode
                    (folder / 'cleanup.stdout.txt').write_bytes(killed.stdout)
                    (folder / 'cleanup.stderr.txt').write_bytes(killed.stderr)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                record['exit_code'] = process.wait(timeout=5)
                record['cleanup']['parent_reaped'] = True
                record['reason'] = 'Stage timeout: no acceptance conclusion from incomplete execution'
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record['reason'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                    record['cleanup']['parent_reaped'] = True
                except (OSError, subprocess.SubprocessError) as exc:
                    record['cleanup']['cleanup_error'] = str(exc)
    record['finished_at'] = now()
    record['duration_ms'] = round((time.monotonic() - started) * 1000)
    if not record['timed_out'] and record['exit_code'] is not None and not record['reason']:
        record['status'] = 'passed' if record['exit_code'] == 0 else 'failed'
    return record


def assess(record, run_dir, repo):
    name = record['stage']
    folder = run_dir / name
    if record['status'] == 'blocked':
        return
    try:
        if name in ('platform_pytest', 'repair_gates'):
            path = folder / 'junit.xml'
            record['junit_path'] = f'{name}/junit.xml' if path.exists() else None
            if not path.is_file():
                raise ValueError('Required JUnit report was not produced')
            counts, skipped, failed = junit_counts(path)
            record.update(counts=counts, skipped_tests=skipped, failed_tests=failed)
            if name == 'repair_gates':
                required = json.loads((repo / 'acceptance_tests/case_map.json').read_text(encoding='utf-8'))['required_testcases']
                observed = [c.get('name') for c in ET.parse(path).getroot().findall('.//testcase')]
                record['required_case_count'] = len(required)
                record['observed_testcases'] = observed
                if not required or len(observed) != len(required) or set(observed) != set(required):
                    raise ValueError('Collected repair gates do not match the required case manifest')
            allowed = [s for s in skipped if name == 'platform_pytest'
                       and s['test'].endswith('::test_in_root_symlink_is_rejected_before_resolution')
                       and s['type'] == 'pytest.skip'
                       and 'Host does not permit creating symlinks' in s['message']]
            record['allowed_skips'] = allowed
            if counts['errors']:
                record.update(status='blocked', reason='Collection/setup/teardown errors: execution evidence is incomplete')
            elif skipped != allowed:
                record.update(status='blocked', reason='Unexpected skip or xfail; not all required gates executed')
            elif counts['failed'] or record['exit_code'] != 0:
                record.update(status='failed', reason='Test assertions failed; inspect JUnit and raw logs')
            elif counts['passed'] == 0:
                record.update(status='blocked', reason='No tests actually completed successfully')
            else:
                record['status'] = 'passed'
        elif name in ('doctor', 'validate_config'):
            parsed = json.loads((folder / 'stdout.txt').read_text(encoding='utf-8-sig'))
            record['parsed_result'] = parsed
            if name == 'doctor':
                # Offline environment checks do not require paid API or a verified
                # Docker host; retain every blocker instead of erasing it.
                if parsed.get('baseline_available') is not True:
                    record.update(status='blocked', reason='Doctor reports baseline environment unavailable')
                record['scope'] = 'Read-only environment inventory; not live-model or isolation acceptance'
            elif parsed.get('errors') != []:
                record.update(status='failed', reason='Configuration validator reported errors')
        elif name == 'node_ui':
            output = (folder / 'stdout.txt').read_text(encoding='utf-8', errors='replace')
            counts = {}
            for field in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo'):
                values = re.findall(r'^# ' + field + r' (\d+)\s*$', output, re.M)
                counts[field] = int(values[-1]) if values else None
            record['counts'] = counts
            if any(v is None for v in counts.values()) or not counts['tests']:
                raise ValueError('Node TAP summary is absent or incomplete')
            if counts['skipped'] or counts['todo'] or counts['cancelled']:
                record.update(status='blocked', reason='Node tests did not execute every required case')
            elif counts['fail'] or record['exit_code'] != 0:
                record.update(status='failed', reason='Node UI contract assertions failed')
        elif name == 'c_controls':
            summaries = list((folder / 'controls').glob('*/summary.json'))
            if len(summaries) != 1:
                raise ValueError('Expected exactly one new C-control summary')
            summary_path = summaries[0]
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            record['control_summary_path'] = summary_path.relative_to(run_dir).as_posix()
            record['control_run_id'] = summary.get('run_id')
            records = summary.get('records', [])
            record['scenario_count'] = len(records)
            if len(records) != 12 or summary.get('all_control_checks_passed') is not True:
                record.update(status='failed', reason='C fixed-control scenarios did not all meet their expected outcomes')
            for control in records:
                result = control['result']
                if control.get('control_check_passed') is not True:
                    record.update(status='failed', reason='A C-control scenario failed')
                for key in ('stdout_path', 'stderr_path'):
                    target = (summary_path.parent / 'artifacts' / result[key]).resolve()
                    if not target.is_relative_to(summary_path.parent.resolve()) or not target.is_file():
                        raise ValueError('A C control has a missing or escaping evidence path')
        save(folder / 'step.json', record)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        record.update(status='blocked', reason='Evidence validation failed: ' + str(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--work-root', type=Path, help='Short temporary parent directory; each invocation creates a unique child and preserves a copy in the evidence directory')
    parser.add_argument('--node', default=None, help='Node executable path; otherwise find node on PATH')
    parser.add_argument('--archive-provenance', type=Path, help='Optional source archive SHA/commit declaration; never used as an actual Git HEAD')
    args = parser.parse_args()
    repo = args.repository.resolve()
    output = (args.output_root or repo / 'evidence/c-review').resolve()
    if not (repo / 'app/cli.py').is_file() or not (repo / 'pytest.ini').is_file():
        parser.error('--repository must point to the Code-Fix repository root')
    if output == repo or any(output.is_relative_to(repo / directory) for directory in SOURCE_DIRS):
        parser.error('--output-root cannot be a tested source directory')
    run_id = 'C-acceptance-' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    run_dir = output / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    scratch_parent = (args.work_root or Path(tempfile.gettempdir())).resolve()
    if scratch_parent == repo or any(scratch_parent.is_relative_to(repo / directory) for directory in SOURCE_DIRS):
        parser.error('--work-root cannot be a tested source directory')
    scratch = scratch_parent / ('ca-' + uuid.uuid4().hex[:8])
    scratch.mkdir(parents=True, exist_ok=False)
    before = snapshot(repo)
    provenance = args.archive_provenance
    if provenance is None and (repo / 'archive-source.json').is_file():
        provenance = repo / 'archive-source.json'
    identity = repository_identity(repo, provenance)
    checks = check_environment(repo)
    save(run_dir / 'environment.json', checks)
    save(run_dir / 'source-before.json', before)
    env = environment(run_dir)
    steps = []
    python = [sys.executable, '-B', '-X', 'utf8']
    node = args.node or shutil.which('node')
    commands = [
        ('doctor', [*python, '-m', 'app.cli', 'doctor'], 30),
        ('validate_config', [*python, '-m', 'app.cli', 'validate-config'], 30),
        ('platform_pytest', [*python, '-m', 'pytest', 'tests', 'c_tests', '-q', '-p', 'no:cacheprovider',
                             '--basetemp=' + str(scratch / 'p'),
                             '--junitxml=' + str(run_dir / 'platform_pytest/junit.xml')], 600),
        ('node_ui', [str(node), '--test', '--test-reporter=tap', 'tests/ui_prototype.test.cjs', 'tests/ui_budget.test.cjs'], 60),
        ('c_controls', [*python, 'scripts/check_c_controls.py', '--output-root', str(run_dir / 'c_controls/controls')], 180),
        ('repair_gates', [*python, '-m', 'pytest', 'acceptance_tests', '-q', '-p', 'no:cacheprovider', '-o', 'xfail_strict=true',
                          '--basetemp=' + str(scratch / 'g'),
                          '--junitxml=' + str(run_dir / 'repair_gates/junit.xml')], 180),
    ]
    for name, argv, timeout in commands:
        missing = None
        if checks['issues']:
            missing = 'Python/locked dependency preflight failed; no test process started'
        elif name == 'node_ui' and not node:
            missing = 'Node executable unavailable; provide --node PATH'
        elif name == 'repair_gates' and not list((repo / 'acceptance_tests').glob('test_*.py')):
            missing = 'Independent repair acceptance_tests are missing; cannot claim acceptance'
        if missing:
            record = {'stage': name, 'status': 'blocked', 'exit_code': None, 'reason': missing,
                      'verification_status': 'not_run'}
        else:
            print(f'[{name}] running', flush=True)
            record = execute(name, argv, repo, run_dir, env, timeout)
            assess(record, run_dir, repo)
            if name in ('platform_pytest', 'repair_gates'):
                source = scratch / ('p' if name == 'platform_pytest' else 'g')
                record['work_preservation'] = preserve_work(source, run_dir / name / 'work')
            save(run_dir / name / 'step.json', record)
        steps.append(record)
        print(f'[{name}] {record["status"]}: {record.get("reason") or "see original evidence"}', flush=True)
    after = snapshot(repo)
    save(run_dir / 'source-after.json', after)
    stable = before == after
    code = 2 if checks['issues'] or not stable or any(s['status'] == 'blocked' for s in steps) else 1 if any(s['status'] == 'failed' for s in steps) else 0
    report = {'run_id': run_id, 'finished_at': now(), 'repository': str(repo),
              'temporary_work_root': str(scratch),
              'source_identity': identity, 'source_unchanged_during_checks': stable,
              'source_snapshot_sha256': digest(run_dir / 'source-before.json'),
              'python_executable': sys.executable, 'python_version': sys.version,
              'exit_code': code, 'acceptance_status': {0: 'passed_offline_scope', 1: 'defects_detected', 2: 'blocked_or_incomplete'}[code],
              'environment': checks, 'steps': steps, 'model_api_called': False,
              'docker_execution_performed': False, 'candidate_execution_performed': False,
              'activity_attestation': 'Scope declaration based on reviewed tests and fixtures, not an independent runtime network/process audit. Re-review test code before running against a changed repository.',
              'isolation_verified': False, 'real_model_final_acceptance': 'not_run',
              'scope': 'C offline reviewed platform/fixture regressions and repair gates; simulated model/runner responses are not real model or Docker evidence',
              'signoff': 'C local execution evidence only; no B or A signature filled automatically'}
    case_map = repo / 'acceptance_tests/case_map.json'
    if case_map.is_file():
        shutil.copyfile(case_map, run_dir / 'repair-case-map.json')
        report['repair_case_map_path'] = 'repair-case-map.json'
    save(run_dir / 'summary.json', report)
    lines = ['# C 离线验收结果', '', f'运行编号：`{run_id}`。退出码：{code}。', '',
             f'结论：{report["acceptance_status"]}。原平台回归与返修门槛分别登记；旧平台测试通过不能替代返修通过。', '',
             '| 检查 | 状态 | 退出码 | 原因 |', '| --- | --- | --- | --- |']
    for step in steps:
        reason = (step.get('reason') or '见原始记录').replace('|', '/').replace('\n', ' ')
        lines.append(f'| {step["stage"]} | {step["status"]} | {step.get("exit_code")} | {reason} |')
    lines.extend(['', '平台pytest允许且单列宿主符号链接权限不足这一指定跳过项；返修门槛不允许skip/xfail代替通过。',
                  '完整记录、失败用例、计数和源码哈希见summary.json、各步step.json和JUnit。',
                  '未调用真实模型，未执行Docker/公开候选，没有真实final签收；不修改预算或隔离批准状态。',
                  'git_commit为空时仅有归档声明和文件哈希，不能登记为已检出该提交。', ''])
    (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    hashes = {p.relative_to(run_dir).as_posix(): digest(p) for p in sorted(run_dir.rglob('*'))
              if p.is_file() and not p.is_symlink()}
    save(run_dir / 'sha256.json', {'scope': 'Evidence files excluding this checksum index and symlinks', 'sha256': hashes})
    print('Evidence: ' + str(run_dir), flush=True)
    return code


if __name__ == '__main__':
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print('Acceptance interrupted; evidence is incomplete (exit 2).', file=sys.stderr)
        exit_code = 2
    except Exception:
        traceback.print_exc()
        print('Acceptance infrastructure error; no complete acceptance conclusion (exit 2).', file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)
