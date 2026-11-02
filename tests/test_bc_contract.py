"""Integration contract checks; no model calls or candidate execution."""
from pathlib import Path

import pytest

import c_runner
from app import runner


def test_b_and_c_measure_the_same_version(tmp_path):
    (tmp_path / 'a.py').write_text('x = 1\n', encoding='utf-8')
    b_version = runner.code_version(tmp_path, ('a.py',))
    assert b_version == c_runner.compute_code_version(tmp_path, ['a.py'])
    assert len(b_version.removeprefix('sha256:')) == 64


def test_registered_result_is_forwarded_with_its_artifact_base(tmp_path, monkeypatch):
    calls = []
    expected = {'status': 'blocked', 'verification_status': 'not_run',
                'artifact_paths_relative_to': 'artifact_dir',
                'stdout_path': 'run/call/stdout.txt', 'code_version': None}

    def fake_run(**kwargs):
        calls.append(kwargs)
        return expected.copy()

    monkeypatch.setattr(c_runner, 'run_tests', fake_run)
    result = runner.run_tests(run_id='r', task_id='t', workspace=tmp_path,
                              test_profile='leap_bad', timeout_seconds=2,
                              code_version='sha256:test', artifact_dir=tmp_path / 'logs')
    assert calls[0]['test_profile'] == 'leap_bad'
    assert calls[0]['code_version'] == 'sha256:test'
    assert result['artifact_paths_relative_to'] == 'artifact_dir'
    assert result['stdout_path'] == expected['stdout_path']
    assert result['code_version_scheme'] == 'sha256-canonical-files-v1'


def test_arbitrary_command_api_is_no_longer_an_execution_entry(tmp_path):
    with pytest.raises(TypeError):
        runner.run_tests(tmp_path, ['arbitrary-command'], 2, tmp_path, 'call')
    assert list(tmp_path.iterdir()) == []


def test_unregistered_profile_does_not_start_a_process(tmp_path):
    result = runner.run_tests(run_id='r', task_id='t', workspace=tmp_path,
                              test_profile='arbitrary-shell', timeout_seconds=2,
                              code_version='sha256:test', artifact_dir=tmp_path / 'logs')
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'not_run'
    assert result['command'] is None
