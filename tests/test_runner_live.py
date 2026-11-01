"""Fixed local subprocess fixtures; no model/API calls or candidate execution."""
import json
import sys
from pathlib import Path

from app import cli
from app.runner import run_tests


def write_four_checks(root: Path):
    (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_fixed.py').write_text(
        '\n'.join(f'def test_fixed_{i}():\n    assert {i} == {i}\n' for i in range(4)),
        encoding='utf-8',
    )


def test_real_pytest_all_pass_without_failed_summary(tmp_path):
    write_four_checks(tmp_path)
    result = run_tests(tmp_path, [sys.executable, '-m', 'pytest', 'tests/', '-q'], 15,
                       tmp_path / 'logs', 'all-pass', expected_test_count=4)
    assert result['status'] == 'ok'
    assert result['verification_status'] == 'passed'
    assert result['counts']['passed'] == 4
    assert result['exit_code'] == 0


def test_missing_executable_is_environment_error(tmp_path):
    result = run_tests(tmp_path, [str(tmp_path / 'nonexistent-code-fix-command')], 2,
                       tmp_path / 'logs', 'missing', expected_test_count=4)
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'inconclusive'
    assert result['failure_class'] == 'environment_error'
    assert result['exit_code'] is None


def test_timeout_preserves_stdout_stderr_once(tmp_path):
    program = "import sys,time; print('OUT-MARKER',flush=True); print('ERR-MARKER',file=sys.stderr,flush=True); time.sleep(10)"
    logs = tmp_path / 'logs'
    result = run_tests(tmp_path, [sys.executable, '-c', program], 1, logs, 'timeout')
    assert result['status'] == 'timeout'
    assert result['verification_status'] == 'inconclusive'
    assert result['exit_code'] is None
    assert (logs / result['stdout_path']).read_text(encoding='utf-8').count('OUT-MARKER') == 1
    assert (logs / result['stderr_path']).read_text(encoding='utf-8').count('ERR-MARKER') == 1


def test_skipped_checks_do_not_pass(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_skipped.py').write_text(
        'import pytest\n@pytest.mark.parametrize("n", range(4))\ndef test_skip(n):\n    pytest.skip("fixture")\n',
        encoding='utf-8',
    )
    result = run_tests(tmp_path, [sys.executable, '-m', 'pytest', 'tests/', '-q'], 15,
                       tmp_path / 'logs', 'skipped', expected_test_count=4)
    assert result['status'] == 'blocked'
    assert result['verification_status'] == 'inconclusive'


def test_passing_baseline_remains_tool_check(tmp_path, monkeypatch, capsys):
    # Synthetic, fixed passing task checks summary semantics, not repair ability.
    task_root = tmp_path / 'day1-kit'
    write_four_checks(task_root)
    (task_root / 'code').mkdir()
    (task_root / 'code/leap_year.py').write_text('# fixed task metadata fixture\n', encoding='utf-8')
    (task_root / 'configs').mkdir()
    (tmp_path / 'configs/tasks').mkdir(parents=True)
    source_config = cli.ROOT / 'configs/tasks/leap-baseline.json'
    (tmp_path / 'configs/tasks/leap-baseline.json').write_bytes(source_config.read_bytes())
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    assert cli.run_baseline() == 0
    output = json.loads(capsys.readouterr().out)
    run_dir = tmp_path / output['evidence_path']
    summary = json.loads((run_dir / 'run_summary.json').read_text(encoding='utf-8'))
    assert summary['run_type'] == 'tool_check'
    assert summary['dev_result'] == 'passed'
    assert summary['status'] == 'stopped'
    assert summary['final_result'] == 'not_run'
    events = [json.loads(line) for line in (run_dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events[-1]['event'] == 'final'
    assert all(event['event'] != 'model_call' for event in events)
