"""Protocol fault injections are not target-program tests or repair scores."""
import json
from pathlib import Path

from c_runner import compute_code_version, prepare_fixture, run_tests, parse_evidence
import c_runner.runner as runner


def test_junit_success_cannot_override_structured_skip(tmp_path):
    node = 'tests/test_one.py::test_one'
    profile = {'expected_nodeids': [node]}
    junit = tmp_path / 'junit.xml'
    junit.write_text('<testsuites><testsuite><testcase classname="tests.test_one" name="test_one"/></testsuite></testsuites>')
    reports = [{'nodeid': node, 'when': stage, 'outcome': 'skipped' if stage == 'call' else 'passed'}
               for stage in ('setup', 'call', 'teardown')]
    structured = tmp_path / 'structured.json'
    structured.write_text(json.dumps({'collected_nodeids': [node], 'reports': reports,
                                      'collection_errors': [], 'session_finished': True, 'exit_code': 0}))
    result = parse_evidence(junit, structured, profile, 0)
    assert result['verification_status'] == 'inconclusive'
    assert 'disagree' in result['reason']


def test_start_failure_has_result_and_missing_junit_reason(tmp_path, monkeypatch):
    work = prepare_fixture('leap_good', tmp_path / 'work')
    version = compute_code_version(work, 'leap_app/leap_year.py')
    def failed_start(*args, **kwargs):
        raise FileNotFoundError('controlled process-start failure')
    monkeypatch.setattr(runner.subprocess, 'Popen', failed_start)
    result = run_tests('start-failure', 'fixture', work, 'leap_good', 2, version, tmp_path / 'artifacts')
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'not_run'
    assert result['failure_class'] == 'environment_error'
    assert result['junit_path'] is None and result['junit_missing_reason']
    assert (tmp_path / 'artifacts' / result['result_path']).is_file()


def test_unknown_profile_never_executes(tmp_path):
    result = run_tests('unknown-profile', 'fixture', tmp_path, 'arbitrary-shell', 2,
                       'sha256:unknown', tmp_path / 'artifacts')
    assert result['status'] == 'blocked'
    assert result['command'] is None


def test_version_accepts_single_path_and_path_list(tmp_path):
    work = prepare_fixture('leap_good', tmp_path / 'work')
    assert compute_code_version(work, 'leap_app/leap_year.py') == compute_code_version(work, ['leap_app/leap_year.py'])
