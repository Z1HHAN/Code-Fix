"""Deterministic checks of pre-reviewed immutable fixtures.

This is not an isolation boundary. Candidate execution is always blocked.
All artifact paths in ToolResult are relative to the supplied artifact_dir.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parent
INTERFACE_VERSION = '0.2-20261101-draft'
CODE_VERSION_SCHEME = 'sha256-canonical-files-v1'
SHANGHAI = timezone(timedelta(hours=8))
COUNTS = ('collected', 'passed', 'failed', 'errors', 'skipped')


class Rejected(ValueError):
    """A controller input or fixture failed a pre-execution check."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _version(value):
    return 'sha256:' + hashlib.sha256(_canonical(value)).hexdigest()


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise Rejected('File paths must be nonempty forward-slash relative strings')
    p = Path(value)
    if p.is_absolute() or p.drive or '..' in p.parts or ':' in value:
        raise Rejected('File path escapes the task workspace')
    return p


def _no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise Rejected('Symlinks and junctions are not accepted')


def _hash_files(workspace, paths):
    root = Path(workspace).resolve()
    _no_links(workspace)
    result = {}
    for value in paths:
        relative = _relative(value)
        file = root / relative
        _no_links(file)
        if not file.is_file() or not file.resolve().is_relative_to(root):
            raise Rejected('Missing or out-of-scope task file: ' + value)
        result[relative.as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def compute_code_version(workspace, source_paths):
    """Hash canonical {relative_path: sha256(file_bytes)} for explicit sources."""
    if isinstance(source_paths, (str, Path)):
        source_paths = [str(source_paths).replace('\\', '/')]
    if not isinstance(source_paths, (list, tuple)) or not source_paths:
        raise Rejected('source_paths must list source files')
    return _version(_hash_files(workspace, source_paths))


def compute_test_suite_version(profile):
    sources = set(profile['source_paths'])
    files = {p: h for p, h in profile['approved_files'].items() if p not in sources}
    return _version({'files': files, 'expected_nodeids': profile['expected_nodeids'],
                     'allowed_skips': profile.get('allowed_skips', {})})


def _profile(name):
    if not isinstance(name, str):
        raise Rejected('test_profile must be a registered string')
    path = PACKAGE / 'profiles.json'
    stat = path.stat()
    data = deepcopy(_profile_registry(str(path), stat.st_mtime_ns, stat.st_size))
    if data.get('schema_version') != 1 or name not in data['profiles']:
        raise Rejected('Unknown test_profile; arbitrary commands are not accepted')
    profile = data['profiles'][name]
    for p in profile['approved_files']:
        _relative(p)
    for p in profile['test_paths']:
        _relative(p)
        if p.startswith('-'):
            raise Rejected('Test paths cannot be command-line options')
    if len(profile['expected_nodeids']) != len(set(profile['expected_nodeids'])):
        raise Rejected('Registry contains duplicate expected test identities')
    return profile


@lru_cache(maxsize=4)
def _profile_registry(path, modified_ns, size):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _snapshot(workspace):
    root = Path(workspace)
    _no_links(root)
    if not root.is_dir():
        raise Rejected('Workspace does not exist')
    # Check ancestors once, then every entry (including directories) before
    # descending. _hash_files remains an independently checked public-code path.
    resolved = root.resolve()
    files = {}
    def walk_error(error):
        raise error
    for directory, directories, filenames in root.walk(on_error=walk_error, follow_symlinks=False):
        for name in (*directories, *filenames):
            p = directory / name
            if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
                raise Rejected('Symlinks and junctions are not accepted')
            if p.is_file():
                relative = _relative(p.relative_to(root).as_posix())
                if not p.resolve().is_relative_to(resolved):
                    raise Rejected('Out-of-scope task file: ' + relative.as_posix())
                files[relative.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def prepare_fixture(profile_id, destination):
    """Create a fresh approved task copy; never overwrite an existing directory."""
    profile = _profile(profile_id)
    source = PROJECT / _relative(profile['fixture_dir'])
    if _snapshot(source) != profile['approved_files']:
        raise Rejected('Registered fixture bytes do not match the reviewed catalog')
    destination = Path(destination)
    _no_links(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in profile['approved_files']:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    if _snapshot(destination) != profile['approved_files']:
        raise Rejected('Fixture copy checksum mismatch')
    return destination.resolve()


def _junit_key(nodeid):
    # Match pytest's mangle_test_address: :: inside a parameter id is data.
    address, bracket, parameters = nodeid.partition('[')
    parts = address.split('::')
    parts[-1] += bracket + parameters
    filename = parts[0].removesuffix('.py').replace('/', '.')
    return ('.'.join([filename, *parts[1:-1]]), parts[-1])


def parse_evidence(junit_path, structured_path, profile, exit_code):
    """Validate JUnit and independently recorded pytest identities/phases.

    Counts from absent/malformed reports stay null, never fabricated as zero.
    This reader does not make files produced by arbitrary code trustworthy.
    """
    counts = dict.fromkeys(COUNTS)
    result = {'counts': counts, 'status': 'failed', 'verification_status': 'inconclusive',
              'failure_class': 'not_reproduced', 'reason': None, 'coverage': {}}
    try:
        evidence = json.loads(Path(structured_path).read_text(encoding='utf-8'))
        collected = evidence['collected_nodeids']
        if collected is not None and (not isinstance(collected, list) or not all(isinstance(x, str) for x in collected)):
            raise ValueError('Invalid collected identities')
        if collected is not None:
            counts['collected'] = len(collected)
        reports = evidence['reports']
        if not isinstance(reports, list) or not isinstance(evidence['collection_errors'], list):
            raise ValueError('Invalid structured report records')
        for report in reports:
            if (not isinstance(report, dict) or not isinstance(report.get('nodeid'), str)
                    or report.get('when') not in {'setup', 'call', 'teardown'}
                    or report.get('outcome') not in {'passed', 'failed', 'skipped'}):
                raise ValueError('Malformed per-test phase record')
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['reason'] = 'Structured pytest evidence unavailable or malformed: ' + str(error)
        return result
    try:
        if junit_path is None:
            raise ValueError('JUnit missing')
        tree = ET.parse(junit_path).getroot()
        if tree.tag not in {'testsuites', 'testsuite'}:
            raise ValueError('Unexpected JUnit root')
        cases = tree.findall('.//testcase')
        counts.update(passed=0, failed=0, errors=0, skipped=0)
        identities = []
        for case in cases:
            if not case.get('name') or case.get('classname') is None:
                raise ValueError('JUnit testcase has no identity')
            identities.append((case.get('classname'), case.get('name')))
            has_failure = case.find('failure') is not None
            has_error = case.find('error') is not None
            has_skip = case.find('skipped') is not None
            counts['failed'] += int(has_failure)
            counts['errors'] += len(case.findall('error'))
            counts['skipped'] += int(has_skip)
            counts['passed'] += int(not (has_failure or has_error or has_skip))
    except (OSError, ValueError, ET.ParseError, TypeError) as error:
        counts.update(passed=None, failed=None, errors=None, skipped=None)
        result['reason'] = 'JUnit unavailable or malformed: ' + str(error)
        return result

    expected = profile['expected_nodeids']
    expected_set = set(expected)
    result['coverage'] = {
        'expected_nodeids': expected, 'collected_nodeids': collected,
        'expected_collected_match': Counter(collected or []) == Counter(expected),
        'junit_identities': [list(x) for x in identities],
        'junit_expected_match': Counter(identities) == Counter(_junit_key(n) for n in expected),
        'session_finished': evidence.get('session_finished') is True,
    }
    if evidence.get('session_finished') is not True or evidence.get('exit_code') != exit_code:
        result['reason'] = 'Session completion/exit-code evidence is missing or inconsistent'
        return result
    if evidence['collection_errors']:
        if profile.get('_source_syntax_errors'):
            result.update(status='failed', verification_status='failed', failure_class='syntax_error',
                          reason='Registered source syntax error confirmed without executing source')
        else:
            result.update(failure_class='environment_error', reason='Collection/import failed; no business assertion result')
        return result
    if not expected or not result['coverage']['expected_collected_match']:
        result['reason'] = 'No tests or expected collected identities differ from actual collection'
        return result
    per_node = {node: {} for node in expected}
    for report in reports:
        node = report['nodeid']
        if node not in expected_set or report['when'] in per_node[node]:
            result['reason'] = 'Unexpected test identity or duplicate pytest phase record'
            return result
        per_node[node][report['when']] = report
    for phases in per_node.values():
        if 'setup' not in phases or 'teardown' not in phases:
            result['reason'] = 'A collected test did not complete its setup/teardown lifecycle'
            return result
        if phases['setup']['outcome'] == 'passed' and 'call' not in phases:
            result['reason'] = 'A collected test body did not execute'
            return result
    if counts['errors']:
        result.update(failure_class='environment_error', reason='JUnit contains setup/teardown errors')
        return result
    if not result['coverage']['junit_expected_match']:
        result['reason'] = 'JUnit test identities differ from frozen test identities'
        return result
    cases_by_identity = {(c.get('classname'), c.get('name')): c for c in cases}
    for node, phases in per_node.items():
        case = cases_by_identity[_junit_key(node)]
        actual_flags = (case.find('failure') is not None, case.find('error') is not None,
                        case.find('skipped') is not None)
        expected_flags = (
            phases.get('call', {}).get('outcome') == 'failed',
            any(phases[k]['outcome'] == 'failed' for k in ('setup', 'teardown')),
            any(r['outcome'] == 'skipped' for r in phases.values()),
        )
        if actual_flags != expected_flags:
            result['reason'] = 'JUnit and structured outcomes disagree for a test identity'
            return result
    if counts['skipped']:
        allowed = profile.get('allowed_skips', {})
        skipped = {node for node, phases in per_node.items() if any(r['outcome'] == 'skipped' for r in phases.values())}
        if not allowed or not skipped.issubset(allowed) or counts['passed'] == 0:
            result['reason'] = 'Unapproved skipped tests or no actual passing tests'
            return result
    phase_failed = [r for r in reports if r['outcome'] == 'failed']
    if counts['failed']:
        if exit_code != 1 or not phase_failed:
            result['reason'] = 'Failure report and process exit code disagree'
            return result
        exception_types = {r.get('exception_type') for r in phase_failed}
        failure = 'assertion_failed' if exception_types == {'AssertionError'} else 'code_runtime_error'
        result.update(status='failed', verification_status='failed', failure_class=failure,
                      reason='Frozen native fixture tests completed with a recorded failure')
        return result
    if phase_failed or exit_code != 0:
        result['reason'] = 'JUnit, per-test outcomes and process exit code do not all indicate success'
        return result
    if counts['passed'] + counts['skipped'] != len(expected):
        result['reason'] = 'Completed case count differs from frozen test count'
        return result
    result.update(status='ok', verification_status='passed', failure_class='passed',
                  reason='Expected identities and complete lifecycle verified against JUnit')
    return result


def _environment(temp_dir):
    # Do not inherit arbitrary API, cloud, proxy, Python, or pytest variables.
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PATHEXT'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update({'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '',
                'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                'TEMP': str(temp_dir), 'TMP': str(temp_dir),
                'PATH': str(Path(sys.executable).parent)})
    return env


def _bytes(value):
    if value is None:
        return b''
    return value if isinstance(value, bytes) else str(value).encode('utf-8', errors='replace')


def run_tests(run_id, task_id, workspace, test_profile, timeout_seconds, code_version,
              artifact_dir, *, phase='development', execution_mode='reviewed_fixture'):
    """Run exactly one registered, reviewed fixture in a fresh copied workspace.

    Every call has a unique artifact directory and its own ToolResult. No model
    candidate, shell command, external profile, or isolation override is accepted.
    """
    started = time.monotonic()
    artifact_root = Path(artifact_dir).absolute()
    _no_links(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_root = artifact_root.resolve()
    call_id = 'c-' + uuid.uuid4().hex
    valid_run = isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', run_id) and run_id not in {'.', '..'}
    safe_run = run_id if valid_run else 'rejected-input'
    call_dir = artifact_root / safe_run / call_id
    _no_links(call_dir)
    call_dir.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = call_dir / 'stdout.txt', call_dir / 'stderr.txt'
    junit_path, structured_path = call_dir / 'junit.xml', call_dir / 'pytest_events.json'
    stdout_path.write_bytes(b'')
    stderr_path.write_bytes(b'')
    rel = lambda p: p.relative_to(artifact_root).as_posix()
    result = {
        'interface_version': INTERFACE_VERSION, 'interface_status': 'pending_BC_confirmation',
        'call_id': call_id, 'run_id': str(run_id), 'task_id': str(task_id),
        'tool': 'run_tests', 'phase': phase, 'status': 'blocked',
        'verification_status': 'not_run', 'failure_class': 'permission_violation',
        'exit_code': None, 'counts': dict.fromkeys(COUNTS),
        'stdout_path': rel(stdout_path), 'stderr_path': rel(stderr_path),
        'junit_path': None, 'junit_missing_reason': 'Test process has not started',
        'structured_path': None, 'duration_ms': 0, 'code_version': None,
        'requested_code_version': code_version, 'code_version_scheme': CODE_VERSION_SCHEME, 'test_suite_version': None,
        'expected_test_count': None, 'output_agent': None,
        'artifact_paths_relative_to': 'artifact_dir', 'result_path': rel(call_dir / 'result.json'),
        'isolation_ready': False, 'execution_mode': execution_mode,
        'python_version': sys.version, 'run_type': 'tool_check',
        'started_at': datetime.now(SHANGHAI).isoformat(timespec='milliseconds'),
        'cleanup': {'parent_reaped': False, 'pipes_drained': False,
                    'tree_termination_attempted': False, 'full_process_tree_verified': False,
                    'scope': 'reviewed fixture parent; full process-tree isolation not certified'},
        'reason': None, 'command': None,
    }
    process = None
    executed = False
    try:
        if not valid_run or not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', task_id):
            raise Rejected('run_id/task_id must be safe nonempty identifiers')
        if execution_mode != 'reviewed_fixture':
            raise Rejected('Isolation is unverified: model/unreviewed candidate execution is disabled')
        if phase not in {'development', 'final_check'}:
            raise Rejected('Invalid test phase')
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise Rejected('Reviewed-fixture timeout must be finite, positive and no more than 60 seconds')
        if sys.version_info[:2] != (3, 14):
            result['failure_class'] = 'environment_error'
            raise Rejected('Python 3.14 is required for actual execution')
        profile = _profile(test_profile)
        result.update(test_profile=test_profile, expected_test_count=len(profile['expected_nodeids']),
                      test_suite_version=compute_test_suite_version(profile),
                      run_type=profile.get('run_type', 'tool_check'))
        if _snapshot(workspace) != profile['approved_files']:
            raise Rejected('Workspace differs from the reviewed fixture; execution denied')
        measured = compute_code_version(workspace, profile['source_paths'])
        result['code_version'] = measured
        if measured != code_version:
            raise Rejected('Requested code_version does not match actual approved source bytes')
        work = call_dir / 'work'
        work.mkdir()
        for name in profile['approved_files']:
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(workspace) / name, target)
        if _snapshot(work) != profile['approved_files']:
            raise Rejected('Per-call task copy differs from the approved source')
        configuration = call_dir / 'pytest.ini'
        configuration.write_text('[pytest]\n', encoding='utf-8')
        tmp = call_dir / 'tmp'
        tmp.mkdir()
        command = [sys.executable, '-I', '-B', '-X', 'utf8', str(PACKAGE / 'pytest_entry.py'),
                   str(structured_path), str(junit_path), str(configuration), *profile['test_paths']]
        result['command'] = command
        environment = _environment(tmp)
        result['environment_policy'] = 'minimal_allowlist_no_inherited_credentials_or_plugins'
        result['source_syntax_errors'] = []
        for name in profile['source_paths']:
            if name.endswith('.py'):
                try:
                    compile((work / name).read_bytes(), name, 'exec')
                except SyntaxError:
                    result['source_syntax_errors'].append(name)
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0
        kwargs = {'creationflags': flags} if os.name == 'nt' else {'start_new_session': True}
        if os.name == 'nt':
            from c_runner.windows_job import WindowsJob
            job = WindowsJob()
            start = job.start
        else:
            start = subprocess.Popen
        process = start(command, cwd=work, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        executed = True
        result['process_id'] = process.pid
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            result['cleanup'].update(parent_reaped=True, pipes_drained=True)
        except subprocess.TimeoutExpired as timeout:
            timed_out = True
            result['cleanup']['tree_termination_attempted'] = True
            cleanup = result['cleanup']
            try:
                if os.name == 'nt':
                    job.terminate()
                    cleanup['termination_exit_code'] = 0
                    cleanup['termination_method'] = 'TerminateJobObject'
                    (call_dir / 'cleanup.stdout.txt').write_bytes(b'')
                    (call_dir / 'cleanup.stderr.txt').write_bytes(b'')
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                    cleanup['termination_exit_code'] = 0
            except (OSError, subprocess.TimeoutExpired) as error:
                cleanup['termination_error'] = str(error)
            try:
                stdout, stderr = process.communicate(timeout=5)
                cleanup.update(parent_reaped=True, pipes_drained=True)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=3)
                    cleanup.update(parent_reaped=True, pipes_drained=True)
                except subprocess.TimeoutExpired:
                    stdout, stderr = _bytes(timeout.stdout), _bytes(timeout.stderr)
                    cleanup['termination_error'] = 'Pipes did not drain after bounded cleanup; manual process review required'
                    for stream in (process.stdout, process.stderr):
                        if stream:
                            stream.close()
                    try:
                        process.wait(timeout=1)
                        cleanup['parent_reaped'] = True
                    except subprocess.TimeoutExpired:
                        pass
        stdout_path.write_bytes(_bytes(stdout))
        stderr_path.write_bytes(_bytes(stderr))
        result['exit_code'] = process.returncode
        result['structured_path'] = rel(structured_path) if structured_path.is_file() else None
        if junit_path.is_file():
            result['junit_path'] = rel(junit_path)
            result['junit_missing_reason'] = None
        else:
            result['junit_missing_reason'] = 'Timed out before JUnit was produced' if timed_out else 'Process did not produce JUnit'
        parse_profile = dict(profile, _source_syntax_errors=result['source_syntax_errors'])
        parsed = parse_evidence(junit_path if junit_path.is_file() else None, structured_path, parse_profile, process.returncode)
        result.update(parsed)
        if timed_out:
            result.update(status='timeout', verification_status='inconclusive', failure_class='timeout',
                          reason='Per-call fixture deadline exceeded; inspect cleanup scope and partial logs')
        intact = _snapshot(work) == profile['approved_files']
        original_intact = _snapshot(workspace) == profile['approved_files']
        result['integrity'] = {'executed_copy_unchanged': intact, 'input_workspace_unchanged': original_intact}
        if not intact or not original_intact:
            result.update(status='blocked', verification_status='inconclusive', failure_class='permission_violation',
                          reason='Source/test bytes changed during execution; result is not accepted')
        result['evidence_matches_current_version'] = intact and original_intact and measured == code_version
    except Rejected as error:
        result.update(status='blocked', verification_status='inconclusive' if executed else 'not_run', reason=str(error))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(status='failed' if executed else 'blocked',
                      verification_status='inconclusive' if executed else 'not_run',
                      failure_class='environment_error', reason=type(error).__name__ + ': ' + str(error))
        result['junit_missing_reason'] = 'Execution could not produce a validated JUnit report'
    finally:
        job = locals().get('job')
        if job is not None:
            try:
                result['cleanup']['full_process_tree_verified'] = job.close()
                if not result['cleanup']['full_process_tree_verified']:
                    result.update(status='blocked', verification_status='inconclusive', failure_class='environment_error',
                                  reason='Windows job descendants were not reaped')
            except OSError:
                result['cleanup']['full_process_tree_verified'] = False
                result.update(status='blocked', verification_status='inconclusive', failure_class='environment_error',
                              reason='Windows job cleanup failed')
        if process is not None and process.poll() is None:
            # Last resort for unexpected controller exceptions. This does not
            # certify descendant cleanup; the result remains non-success.
            try:
                process.kill()
                process.communicate(timeout=3)
                result['cleanup']['parent_reaped'] = True
            except (OSError, subprocess.TimeoutExpired):
                result['cleanup']['termination_error'] = 'Parent cleanup needs manual review'
        result['duration_ms'] = round((time.monotonic() - started) * 1000)
        result['finished_at'] = datetime.now(SHANGHAI).isoformat(timespec='milliseconds')
        (call_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
