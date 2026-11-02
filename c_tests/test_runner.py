"""C control checks; never model-repair benchmark results."""
import json
from pathlib import Path

import pytest

from c_runner import (
    compute_code_version,
    compute_test_suite_version,
    parse_evidence,
    prepare_fixture,
    run_tests,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / 'c_runner/profiles.json').read_text(encoding='utf-8'))['profiles']


def invoke(tmp_path, profile='leap_good', **kwargs):
    workspace = tmp_path / 'workspace'
    prepare_fixture(profile, workspace)
    version = compute_code_version(workspace, PROFILES[profile]['source_paths'])
    artifacts = tmp_path / 'artifacts'
    result = run_tests('c-test-run', 'c-control', workspace, profile,
                       kwargs.pop('timeout_seconds', 10),
                       kwargs.pop('code_version', version), artifacts, **kwargs)
    return result, workspace, artifacts


def test_bad_baseline_is_three_passed_one_failed(tmp_path):
    result, _, artifacts = invoke(tmp_path, 'leap_bad')
    assert result['status'] == 'failed'
    assert result['verification_status'] == 'failed'
    assert result['failure_class'] == 'assertion_failed'
    assert result['counts'] == {'collected': 4, 'passed': 3, 'failed': 1, 'errors': 0, 'skipped': 0}
    assert result['exit_code'] == 1
    assert (artifacts / result['junit_path']).is_file()


def test_correct_fixture_is_passed_without_zero_failed_text(tmp_path):
    result, _, artifacts = invoke(tmp_path)
    assert result['status'] == 'ok'
    assert result['verification_status'] == 'passed'
    assert result['counts']['passed'] == 4
    assert result['counts']['failed'] == 0
    assert result['exit_code'] == 0
    assert b'0 failed' not in (artifacts / result['stdout_path']).read_bytes()
    assert result['output_agent'] is None


def test_same_original_tests_have_same_suite_version():
    assert compute_test_suite_version(PROFILES['leap_bad']) == compute_test_suite_version(PROFILES['leap_good'])


@pytest.mark.parametrize('profile', ['leap_all_skipped', 'zero_tests', 'incomplete_tests',
                                     'collection_dependency_error', 'collection_syntax_error',
                                     'code_runtime_error'])
def test_control_failures_never_become_passed(tmp_path, profile):
    result, _, _ = invoke(tmp_path, profile)
    assert result['verification_status'] != 'passed'
    assert result['status'] in {'failed', 'blocked'}


def test_timeout_keeps_partial_bytes_and_returns_record(tmp_path):
    result, _, artifacts = invoke(tmp_path, 'output_timeout', timeout_seconds=2)
    assert result['status'] == 'timeout'
    assert result['verification_status'] == 'inconclusive'
    assert result['failure_class'] == 'timeout'
    assert b'C_TIMEOUT_STDOUT_FLUSHED' in (artifacts / result['stdout_path']).read_bytes()
    assert b'C_TIMEOUT_STDERR_FLUSHED' in (artifacts / result['stderr_path']).read_bytes()
    assert result['junit_path'] is None
    assert result['junit_missing_reason']
    assert result['counts']['collected'] in (None, 1)
    assert all(result['counts'][key] is None for key in ('passed', 'failed', 'errors', 'skipped'))


def test_stale_code_version_blocks_before_test_execution(tmp_path):
    result, _, _ = invoke(tmp_path, code_version='sha256:' + '0' * 64)
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'not_run'
    assert result['exit_code'] is None


def test_untrusted_model_candidate_is_blocked(tmp_path):
    result, _, _ = invoke(tmp_path, execution_mode='model_candidate')
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'not_run'
    assert result['exit_code'] is None


def test_modified_test_file_is_blocked(tmp_path):
    workspace = tmp_path / 'workspace'
    prepare_fixture('leap_good', workspace)
    with (workspace / 'tests/test_leap.py').open('a', encoding='utf-8') as stream:
        stream.write('\n# Tampered control fixture\n')
    version = compute_code_version(workspace, PROFILES['leap_good']['source_paths'])
    result = run_tests('tampered-tests', 'c-control', workspace, 'leap_good', 10,
                       version, tmp_path / 'artifacts')
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'not_run'


def test_each_call_has_unique_paths_and_preserves_prior_results(tmp_path):
    first, workspace, artifacts = invoke(tmp_path)
    before = {p: (artifacts / first[p]).read_bytes() for p in ('stdout_path', 'stderr_path', 'junit_path')}
    second = run_tests('c-test-run', 'c-control', workspace, 'leap_good', 10,
                       first['code_version'], artifacts)
    assert first['call_id'] != second['call_id']
    assert first['stdout_path'] != second['stdout_path']
    for field, content in before.items():
        assert (artifacts / first[field]).read_bytes() == content


@pytest.mark.parametrize('payload', [None, '<not-closed', '<testsuites/>'])
def test_missing_malformed_or_empty_junit_never_passes(tmp_path, payload):
    junit = tmp_path / 'junit.xml'
    if payload is not None:
        junit.write_text(payload, encoding='utf-8')
    structured = tmp_path / 'structured.json'
    nodeids = PROFILES['leap_good']['expected_nodeids']
    structured.write_text(json.dumps({
        'collected_nodeids': nodeids, 'collection_errors': [],
        'session_finished': True, 'exit_code': 0,
        'reports': [{'nodeid': node, 'when': stage, 'outcome': 'passed'}
                    for node in nodeids for stage in ('setup', 'call', 'teardown')],
    }), encoding='utf-8')
    parsed = parse_evidence(junit, structured, PROFILES['leap_good'], 0)
    assert parsed['verification_status'] != 'passed'
    assert not parsed['reason'].startswith('Structured pytest evidence')


def test_prepare_fixture_refuses_existing_destination(tmp_path):
    target = tmp_path / 'workspace'
    prepare_fixture('leap_good', target)
    before = (target / 'leap_app/leap_year.py').read_bytes()
    with pytest.raises((FileExistsError, ValueError, RuntimeError)):
        prepare_fixture('leap_good', target)
    assert (target / 'leap_app/leap_year.py').read_bytes() == before
