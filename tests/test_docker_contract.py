"""Docker argv/control contract only; these tests never certify a Docker host."""
from pathlib import Path
import pytest

from app.docker_runner import DockerRunner, IsolationBlocked


def test_candidate_command_has_no_writable_host_mount_or_inherited_key(tmp_path):
    runner = DockerRunner()
    runner.executable = 'docker-test-stub'
    runner.image = 'sha256:' + 'a'*64
    argv = runner.command('test-only', tmp_path, tmp_path, Path('c_runner/pytest_entry.py'), ['/harness.py'])
    assert '--network=none' in argv and '--read-only' in argv
    assert '--cap-drop=ALL' in argv and '--pids-limit=64' in argv
    assert '--user=65534:65534' in argv and '--memory=256m' in argv
    assert '--pull=never' in argv
    for index, token in enumerate(argv):
        if token == '--mount':
            assert argv[index+1].endswith(',readonly')
    assert not any('API_KEY' in token for token in argv)
    assert '--tmpfs=/out:rw,noexec,nosuid,size=16777216,mode=1777' in argv


def test_unverified_runner_never_executes_candidate(tmp_path):
    with pytest.raises(IsolationBlocked):
        DockerRunner().run(tmp_path, tmp_path / 'log', phase='development', timeout=1, expected_version='any')


def test_public_native_test_path_is_forwarded_without_any_container_execution(tmp_path, monkeypatch):
    from app.runner import code_version
    from app.tasks_v3 import get_task
    task = get_task('qb-gcd')
    work = task.prepare(tmp_path / 'work')
    runner = DockerRunner(task.project)
    runner.ready = True  # Contract test only; no Docker certification.
    seen = []
    def offline_execute(work, output, extra, timeout, folder):
        seen.extend(extra)
        return {'timed_out': True, 'cleanup_passed': True, 'exit_code': None}
    monkeypatch.setattr(runner, '_execute', offline_execute)
    result = runner.run(work, tmp_path / 'evidence', phase='development', timeout=1,
                        expected_version=code_version(work, (task.source,)))
    assert seen == ['/harness.py', 'python_testcases/test_gcd.py']
    assert result['expected_test_count'] == 6 and result['failure_class'] == 'timeout'
