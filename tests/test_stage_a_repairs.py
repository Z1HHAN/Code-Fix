"""Transport/archival contracts only; no model or Docker process execution."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

from app.docker_runner import DockerRunner


def test_c_work_archive_preserves_long_relative_names_and_bytes(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/c_acceptance.py'
    spec = importlib.util.spec_from_file_location('c_acceptance_checked', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'short'
    relative = '/'.join(['nested-artifacts'] * 5) + '/raw.bin'
    file = source / relative
    file.parent.mkdir(parents=True)
    raw = b'raw\r\n\r\r\n\x00'
    file.write_bytes(raw)
    output = tmp_path / ('x' * 100)
    output.mkdir()
    destination = output / 'work'
    assert len(str(destination / relative)) > 260
    result = module.preserve_work(source, destination)
    assert result['format'] == 'zip' and not destination.exists()
    with zipfile.ZipFile(output / 'work.zip') as z:
        assert z.read(relative) == raw
    assert result['file_sha256'][relative] == hashlib.sha256(raw).hexdigest()
    assert file.read_bytes() == raw


def test_docker_transport_uses_filesystem_api_before_reaping_container(tmp_path, monkeypatch):
    runner = DockerRunner()
    runner.executable = 'docker-contract-stub'
    runner.image = 'sha256:' + 'a' * 64
    work, output, folder = (tmp_path / name for name in ('work', 'out', 'log'))
    for p in (work, output, folder): p.mkdir()
    commands = []
    class Process:
        def __init__(self, command, *, stdin, stdout, stderr):
            commands.append(command)
            stdout.write(b'CODEFIX_READY\n'); stdout.flush()
            self.stdin = io.BytesIO()
            self.returncode = 0
        def poll(self): return 0 if self.stdin.closed else None
    def execute(command, **kwargs):
        commands.append(command)
        raw = b''
        if command[1] == 'cp':
            name = command[2].split('/')[-1]
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as z:
                member = tarfile.TarInfo(name); member.size = 2
                z.addfile(member, io.BytesIO(b'{}'))
            raw = stream.getvalue()
        return subprocess.CompletedProcess(command, 0, raw, b'')
    monkeypatch.setattr('app.docker_runner.subprocess.Popen', Process)
    monkeypatch.setattr('app.docker_runner.subprocess.run', execute)
    result = runner._execute(work, output, ['/harness.py'], 2, folder)
    assert result['artifact_channel'] == 'docker-cp'
    assert (output / 'junit.xml').read_bytes() == b'{}'
    assert [x[1] for x in commands[1:]] == ['cp', 'cp', 'inspect', 'rm']


def test_docker_readiness_without_report_is_rejected_and_cleaned(tmp_path, monkeypatch):
    runner = DockerRunner(); runner.executable = 'docker-contract-stub'
    work, output, folder = (tmp_path / name for name in ('work', 'out', 'log'))
    for p in (work, output, folder): p.mkdir()
    calls = []
    class Process:
        def __init__(self, command, **kwargs):
            kwargs['stdout'].write(b'CODEFIX_READY\n'); kwargs['stdout'].flush()
            self.stdin = io.BytesIO(); self.returncode = 0
        def poll(self): return 0 if self.stdin.closed else None
    def execute(command, **kwargs):
        calls.append(command[1])
        if command[1] == 'cp': raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr('app.docker_runner.subprocess.Popen', Process)
    monkeypatch.setattr('app.docker_runner.subprocess.run', execute)
    result = runner._execute(work, output, ['/harness.py'], 2, folder)
    assert result['artifact_channel'] == 'rejected'
    assert calls == ['cp', 'inspect', 'rm']
    assert not (output / 'junit.xml').exists()


def test_docker_inspect_failure_still_attempts_cleanup(tmp_path, monkeypatch):
    runner = DockerRunner(); runner.executable = 'docker-contract-stub'
    paths = [tmp_path / name for name in ('work', 'out', 'log')]
    for path in paths: path.mkdir()
    class Process:
        def __init__(self, command, **kwargs):
            self.stdin = io.BytesIO(); self.returncode = 0
        def poll(self): return 0
    calls = []
    def execute(command, **kwargs):
        calls.append(command[1])
        if command[1] == 'inspect': raise subprocess.TimeoutExpired(command, 10)
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr('app.docker_runner.subprocess.Popen', Process)
    monkeypatch.setattr('app.docker_runner.subprocess.run', execute)
    result = runner._execute(*paths[:2], ['-c', 'pass'], 2, paths[2])
    assert calls == ['inspect', 'rm']
    assert result['exit_code'] == 0 and result['cleanup_passed']
    assert 'TimeoutExpired' in (paths[2] / 'cleanup.json').read_text()


def test_docker_cleanup_timeout_returns_failed_cleanup(tmp_path, monkeypatch):
    runner = DockerRunner(); runner.executable = 'docker-contract-stub'
    paths = [tmp_path / name for name in ('work', 'out', 'log')]
    for path in paths: path.mkdir()
    class Process:
        def __init__(self, command, **kwargs):
            self.stdin = io.BytesIO(); self.returncode = 0
        def poll(self): return 0
    def execute(command, **kwargs):
        if command[1] == 'rm': raise subprocess.TimeoutExpired(command, 10)
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr('app.docker_runner.subprocess.Popen', Process)
    monkeypatch.setattr('app.docker_runner.subprocess.run', execute)
    result = runner._execute(*paths[:2], ['-c', 'pass'], 2, paths[2])
    assert result['exit_code'] == 0 and not result['cleanup_passed']
    assert 'TimeoutExpired' in (paths[2] / 'cleanup.json').read_text()
