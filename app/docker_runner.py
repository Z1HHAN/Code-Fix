"""Linux Docker candidate runner. Never falls back to host execution.

Requires a locally built content-addressed image, explicit execution approval,
and a real control probe. Missing Docker or probe failure denies execution.
"""
import json
import io
import tarfile
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from c_runner.runner import compute_test_suite_version, parse_evidence
from .runner import code_version
from .tasks_v3 import get_task

ROOT = Path(__file__).resolve().parents[1]


class IsolationBlocked(ValueError):
    pass


class DockerRunner:
    mode = 'docker'

    def __init__(self, project='leap'):
        self.task = get_task(project)
        self.executable = shutil.which('docker')
        self.image = os.environ.get('CODE_FIX_DOCKER_IMAGE', '')
        self.ready = False

    def command(self, name, work, out, entry, extra):
        for path in (work, out, entry):
            if ',' in str(path) or path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise IsolationBlocked('挂载目录包含不支持字符或链接')
        return [self.executable, 'run', '-i', '--name', name, '--pull=never', '--network=none', '--read-only',
                '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=256m',
                '--memory-swap=256m', '--cpus=1', '--user=65534:65534', '--ulimit=nofile=128:128',
                '--tmpfs=/tmp:rw,noexec,nosuid,size=67108864', '--log-driver=none',
                '--tmpfs=/out:rw,noexec,nosuid,size=16777216,mode=1777',
                '--mount', f'type=bind,source={work.resolve()},target=/w,readonly',
                '--mount', f'type=bind,source={entry.resolve()},target=/entry.py,readonly',
                '--mount', f'type=bind,source={(ROOT / "docker/candidate_entry.py").resolve()},target=/harness.py,readonly',
                '--workdir=/w', '--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1', '--env=PYTHONDONTWRITEBYTECODE=1',
                '--entrypoint=python', self.image, '-I', '-B', '-X', 'utf8', *extra]

    def preflight(self, evidence_dir):
        folder = Path(evidence_dir)
        folder.mkdir(parents=True, exist_ok=True)
        report = {'isolation_ready': False, 'image': self.image, 'reason': None}
        try:
            if os.environ.get('CODE_FIX_EXECUTION_APPROVED') != '1':
                raise IsolationBlocked('需确认不可信代码执行授权：CODE_FIX_EXECUTION_APPROVED=1')
            if not self.executable:
                raise IsolationBlocked('找不到Docker；禁止回退宿主执行')
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', self.image):
                raise IsolationBlocked('CODE_FIX_DOCKER_IMAGE须为已构建镜像的完整sha256 ID')
            info = subprocess.run([self.executable, 'info', '--format', '{{json .}}'], capture_output=True, timeout=15, check=True)
            daemon = json.loads(info.stdout)
            if daemon.get('OSType') != 'linux':
                raise IsolationBlocked('当前实现要求Linux容器引擎')
            inspect = subprocess.run([self.executable, 'image', 'inspect', self.image], capture_output=True, timeout=10, check=True)
            image = json.loads(inspect.stdout)[0]
            if image.get('Id') != self.image or image.get('Config', {}).get('Volumes'):
                raise IsolationBlocked('镜像身份不符或含未批准卷')
            # Fixed trusted probe only. This does not execute a model candidate.
            work, output = folder / 'probe-work', folder / 'probe-output'
            work.mkdir(exist_ok=True)
            output.mkdir(exist_ok=True)
            probe = "import json,os,socket; denied=False\ntry:\n open('/w/should-not-write','w')\nexcept OSError:\n denied=True\nprint(json.dumps({'uid':os.getuid(),'readonly':denied,'interfaces':[x[1] for x in socket.if_nameindex()]}))"
            raw = self._execute(work, output, ['-c', probe], 10, folder)
            data = json.loads((folder / raw['stdout_path']).read_text(encoding='utf-8'))
            controls = json.loads((folder / 'container.json').read_text(encoding='utf-8'))[0]['HostConfig']
            if raw['exit_code'] != 0 or not raw['cleanup_passed'] or data['uid'] == 0 or not data['readonly'] or data['interfaces'] != ['lo']:
                raise IsolationBlocked('隔离探针失败')
            if not (controls['ReadonlyRootfs'] and controls['NetworkMode'] == 'none' and controls['Memory'] == 268435456
                    and controls['PidsLimit'] == 64 and 'ALL' in controls['CapDrop']):
                raise IsolationBlocked('容器运行限制与要求不一致')
            self.ready = True
            report.update(isolation_ready=True, engine_id=daemon.get('ID'), controls=controls,
                          scope='Linux Docker probe; not VM-equivalent isolation certification')
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
            report.update(error_type=type(exc).__name__, reason=str(exc) if isinstance(exc, IsolationBlocked) else 'Docker探针执行失败')
        (folder / 'isolation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report

    def _execute(self, work, output, extra, timeout, folder):
        name = 'codefix-' + uuid.uuid4().hex
        command = self.command(name, work, output, ROOT / 'c_runner/pytest_entry.py', extra)
        started = time.monotonic()
        timed_out = False
        exit_code = None
        artifact_channel = None
        artifact_error = None
        with (folder / 'stdout.txt').open('wb') as stdout, (folder / 'stderr.txt').open('wb') as stderr:
            try:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
                try:
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        if extra and extra[0] == '/harness.py' and artifact_channel is None:
                            lines = (folder / 'stdout.txt').read_bytes().splitlines()
                            if b'CODEFIX_READY' in lines:
                                try:
                                    if lines.count(b'CODEFIX_READY') != 1 or any(x.startswith(b'CODEFIX_REPORT:') for x in lines):
                                        raise ValueError('候选stdout包含伪造报告或重复就绪标记')
                                    # Report bytes come from Docker's filesystem
                                    # API, never from a candidate's stdout packet.
                                    for filename in ('junit.xml', 'pytest_events.json'):
                                        copied = subprocess.run([self.executable, 'cp', name+':/out/'+filename, '-'],
                                                                capture_output=True, timeout=max(.01, deadline-time.monotonic()), check=True)
                                        if len(copied.stdout) > 2_200_000:
                                            raise ValueError('报告过大')
                                        with tarfile.open(fileobj=io.BytesIO(copied.stdout)) as archive:
                                            entries = archive.getmembers()
                                            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 2_097_152:
                                                raise ValueError('非法报告归档')
                                            (output / filename).write_bytes(archive.extractfile(entries[0]).read())
                                    artifact_channel = 'docker-cp'
                                except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
                                    artifact_channel = 'rejected'
                                    artifact_error = type(exc).__name__
                                finally:
                                    process.stdin.close()
                        if time.monotonic() >= deadline or any((folder / p).stat().st_size > 8_388_608 for p in ('stdout.txt', 'stderr.txt')):
                            raise subprocess.TimeoutExpired(command, timeout)
                        time.sleep(0.03)
                    exit_code = process.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    process.wait(timeout=5)
                finally:
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
            finally:
                cleanup_passed = False
                cleanup_errors = []
                try:
                    inspected = subprocess.run([self.executable, 'inspect', name], capture_output=True, timeout=10)
                    if inspected.returncode == 0:
                        (folder / 'container.json').write_bytes(inspected.stdout)
                except (OSError, subprocess.SubprocessError) as exc:
                    cleanup_errors.append({'operation': 'inspect', 'error_type': type(exc).__name__})
                try:
                    cleanup = subprocess.run([self.executable, 'rm', '--force', name], capture_output=True, timeout=10)
                    cleanup_passed = cleanup.returncode == 0
                    cleanup_code = cleanup.returncode
                except (OSError, subprocess.SubprocessError) as exc:
                    cleanup_code = None
                    cleanup_errors.append({'operation': 'rm', 'error_type': type(exc).__name__})
                (folder / 'cleanup.json').write_text(json.dumps({'container': name, 'exit_code': cleanup_code,
                    'cleanup_passed': cleanup_passed, 'errors': cleanup_errors}), encoding='utf-8')
        return {'call_id': name, 'exit_code': exit_code, 'timed_out': timed_out, 'duration_ms': round((time.monotonic()-started)*1000),
                'stdout_path': 'stdout.txt', 'stderr_path': 'stderr.txt', 'command': command,
                'artifact_channel': artifact_channel, 'artifact_error': artifact_error,
                'cleanup_passed': cleanup_passed}

    def run(self, work, evidence_dir, *, phase, timeout, expected_version):
        if not self.ready:
            raise IsolationBlocked('未通过实际隔离探针')
        folder = Path(evidence_dir)
        folder.mkdir(parents=True, exist_ok=False)
        work = Path(work)
        measured = code_version(work, self.task.profile['source_paths'])
        if measured != expected_version:
            raise IsolationBlocked('候选版本已改变')
        # Match native test bytes against reviewed registry before container start.
        profile = self.task.profile
        self.task.verify(work, candidate=True)
        # A private reviewed snapshot prevents later mutations of the source
        # workspace from being mislabeled as the version actually executed.
        snapshot = self.task.prepare(folder / 'execution-snapshot')
        (snapshot / self.task.source).write_bytes((work / self.task.source).read_bytes())
        self.task.verify(snapshot, candidate=True)
        if code_version(snapshot, profile['source_paths']) != expected_version:
            raise IsolationBlocked('执行快照版本不一致')
        output = folder / 'output'
        output.mkdir()
        result = self._execute(snapshot, output, ['/harness.py', *profile['test_paths']], timeout, folder)
        result.update(phase=phase, code_version=measured, isolation_ready=True, execution_mode='docker',
                      project=self.task.project,
                      test_suite_version=compute_test_suite_version(profile), expected_test_count=len(profile['expected_nodeids']),
                      junit_path='output/junit.xml', structured_path='output/pytest_events.json')
        if result['timed_out']:
            result.update(status='timeout', verification_status='inconclusive', failure_class='timeout')
        elif not result['cleanup_passed']:
            result.update(status='failed', verification_status='inconclusive', failure_class='environment_error')
        elif result.get('artifact_channel') != 'docker-cp':
            result.update(status='failed', verification_status='inconclusive', failure_class='evidence_channel_tampered')
        else:
            result.update(parse_evidence(output / 'junit.xml', output / 'pytest_events.json', profile, result['exit_code']))
            try:
                compile((snapshot / self.task.source).read_bytes(), self.task.source, 'exec')
            except SyntaxError:
                result.update(status='failed', verification_status='inconclusive', failure_class='syntax_error')
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
