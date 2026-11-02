"""Real registered controls; no model/API calls or candidate execution."""
import json
from pathlib import Path

from app.baseline import baseline_record
from app.runner import code_version, prepare_fixture, run_tests
import c_runner.runner as c_core

ROOT = Path(__file__).resolve().parents[1]


def control(tmp_path, profile, timeout=10):
    work = prepare_fixture(profile, tmp_path / 'work')
    return run_tests(run_id='check', task_id='fixed', workspace=work,
                     test_profile=profile, timeout_seconds=timeout,
                     code_version=code_version(work, ('leap_app/leap_year.py',)),
                     artifact_dir=tmp_path / 'logs')


def test_real_pytest_all_pass_without_failed_summary(tmp_path):
    result = control(tmp_path, 'leap_good')
    assert result['status'] == 'ok' and result['verification_status'] == 'passed'
    assert result['counts'] == dict(collected=4, passed=4, failed=0, errors=0, skipped=0)
    assert result['exit_code'] == 0
    assert result['run_type'] == 'reference_check'


def test_missing_executable_is_environment_error(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('fixed test injection: no executable')
    monkeypatch.setattr(c_core.subprocess, 'Popen', unavailable)
    result = control(tmp_path, 'leap_bad')
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'not_run'
    assert result['failure_class'] == 'environment_error'
    assert result['exit_code'] is None


def test_timeout_preserves_stdout_stderr_once(tmp_path):
    result = control(tmp_path, 'output_timeout', 2)
    assert result['status'] == 'timeout'
    assert result['verification_status'] == 'inconclusive'
    assert result['cleanup']['parent_reaped'] is True
    assert result['cleanup']['pipes_drained'] is True
    logs = tmp_path / 'logs'
    assert (logs / result['stdout_path']).read_text(encoding='utf-8').count('C_TIMEOUT_STDOUT_FLUSHED') == 1
    assert (logs / result['stderr_path']).read_text(encoding='utf-8').count('C_TIMEOUT_STDERR_FLUSHED') == 1


def test_baseline_prepare_error_preserves_final(tmp_path, monkeypatch):
    from app import baseline
    def rejected(*args):
        raise ValueError('fixture unavailable')
    monkeypatch.setattr(baseline, 'prepare_fixture', rejected)
    output = baseline_record(ROOT, output_root=tmp_path)
    assert output['summary']['dev_result'] == 'not_run'
    assert output['summary']['final_result'] == 'not_run'
    events = (Path(output['evidence_path']) / 'events.jsonl').read_text(encoding='utf-8').splitlines()
    assert json.loads(events[-1])['event'] == 'final'


def test_cli_incomplete_test_evidence_has_nonzero_exit(monkeypatch, capsys):
    from app import cli
    monkeypatch.setattr(cli, 'baseline_record', lambda root: {
        'tool_result': {'status': 'failed', 'verification_status': 'inconclusive'}})
    assert cli.run_baseline() == 2
    assert json.loads(capsys.readouterr().out)['tool_result']['verification_status'] == 'inconclusive'


def test_skipped_checks_do_not_pass(tmp_path):
    result = control(tmp_path, 'leap_all_skipped')
    assert result['verification_status'] == 'inconclusive'
    assert result['counts']['skipped'] == 4


def test_passing_tool_result_never_becomes_independent_success(tmp_path, monkeypatch):
    # Summary semantics only: executor replaced, frozen task remains unchanged.
    from app import baseline
    monkeypatch.setattr(baseline, 'run_tests', lambda **kw: {
        'status': 'ok', 'verification_status': 'passed', 'failure_class': 'passed', 'call_id': 'synthetic'})
    output = baseline_record(ROOT, output_root=tmp_path)
    summary = output['summary']
    assert summary['run_type'] == 'tool_check' and summary['dev_result'] == 'passed'
    assert summary['status'] == 'stopped' and summary['final_result'] == 'not_run'
    assert summary['model_api_called'] is False
    events = [json.loads(line) for line in (Path(output['evidence_path']) / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events[-1]['event'] == 'final'
    assert all(event['event'] != 'model_call' for event in events)
