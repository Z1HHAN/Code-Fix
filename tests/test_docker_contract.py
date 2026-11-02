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
