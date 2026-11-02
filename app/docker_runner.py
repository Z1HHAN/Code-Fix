"""Linux Docker candidate runner. Never falls back to host execution.

Requires a locally built content-addressed image, explicit execution approval,
and a real control probe. Missing Docker or probe failure denies execution.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from c_runner.runner import _profile, compute_test_suite_version, parse_evidence
from .runner import code_version

ROOT = Path(__file__).resolve().parents[1]


class IsolationBlocked(ValueError):
    pass


class DockerRunner:
    mode = 'docker'

    def __init__(self):
        self.executable = shutil.which('docker')
        self.image = os.environ.get('CODE_FIX_DOCKER_IMAGE', '')
        self.ready = False

    def command(self, name, work, out, entry, extra):
        for path in (work, out, entry):
            if ',' in str(path) or path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise IsolationBlocked('挂载目录包含不支持字符或链接')
        return [self.executable, 'run', '--name', name, '--pull=never', '--network=none', '--read-only',
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
        with (folder / 'stdout.txt').open('wb') as stdout, (folder / 'stderr.txt').open('wb') as stderr:
            try:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
                try:
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        if time.monotonic() >= deadline or any((folder / p).stat().st_size > 8_388_608 for p in ('stdout.txt', 'stderr.txt')):
                            raise subprocess.TimeoutExpired(command, timeout)
                        time.sleep(0.03)
                    exit_code = process.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    process.wait(timeout=5)
            finally:
                inspected = subprocess.run([self.executable, 'inspect', name], capture_output=True, timeout=10)
                if inspected.returncode == 0:
                    (folder / 'container.json').write_bytes(inspected.stdout)
                cleanup = subprocess.run([self.executable, 'rm', '--force', name], capture_output=True, timeout=10)
                (folder / 'cleanup.json').write_text(json.dumps({'container': name, 'exit_code': cleanup.returncode}), encoding='utf-8')
        return {'call_id': name, 'exit_code': exit_code, 'timed_out': timed_out, 'duration_ms': round((time.monotonic()-started)*1000),
                'stdout_path': 'stdout.txt', 'stderr_path': 'stderr.txt', 'command': command,
                'cleanup_passed': cleanup.returncode == 0}

    def run(self, work, evidence_dir, *, phase, timeout, expected_version):
        if not self.ready:
            raise IsolationBlocked('未通过实际隔离探针')
        folder = Path(evidence_dir)
        folder.mkdir(parents=True, exist_ok=False)
        work = Path(work)
        measured = code_version(work, ('code/leap_year.py',))
        if measured != expected_version:
            raise IsolationBlocked('候选版本已改变')
        # Match native test bytes against reviewed registry before container start.
        import hashlib
        profile = _profile('leap_original')
        for name, digest in profile['approved_files'].items():
            if name != 'code/leap_year.py' and hashlib.sha256((work / name).read_bytes()).hexdigest() != digest:
                raise IsolationBlocked('原生测试或辅助文件被修改')
        output = folder / 'output'
        output.mkdir()
        (output / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        # Bind output is dedicated to this call and contains no other run data.
        output.chmod(0o777)
        (output / 'pytest.ini').chmod(0o644)
        result = self._execute(work, output, ['/harness.py'], timeout, folder)
        result.update(phase=phase, code_version=measured, isolation_ready=True, execution_mode='docker',
                      test_suite_version=compute_test_suite_version(profile), expected_test_count=len(profile['expected_nodeids']),
                      junit_path='output/junit.xml', structured_path='output/pytest_events.json')
        if result['timed_out']:
            result.update(status='timeout', verification_status='inconclusive', failure_class='timeout')
        elif not result['cleanup_passed']:
            result.update(status='failed', verification_status='inconclusive', failure_class='environment_error')
        else:
            import base64
            lines = (folder / 'stdout.txt').read_bytes().splitlines()
            reports = [line[len(b'CODEFIX_REPORT:'):] for line in lines if line.startswith(b'CODEFIX_REPORT:')]
            if len(reports) == 1:
                try:
                    bundle = json.loads(base64.b64decode(reports[0], validate=True))
                    if set(bundle) != {'junit.xml', 'pytest_events.json'}:
                        raise ValueError('非法报告字段')
                    for name, content in bundle.items():
                        (output / name).write_bytes(base64.b64decode(content, validate=True))
                except (ValueError, TypeError):
                    pass
            result.update(parse_evidence(output / 'junit.xml', output / 'pytest_events.json', profile, result['exit_code']))
            try:
                compile((work / 'code/leap_year.py').read_bytes(), 'code/leap_year.py', 'exec')
            except SyntaxError:
                result.update(status='failed', verification_status='inconclusive', failure_class='syntax_error')
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
