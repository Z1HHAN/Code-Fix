import pytest

from app.config import read_json
from app.decision import DecisionEngine
from app.security import PermissionViolation, normalise_relpath, validate_changes
from pathlib import Path


@pytest.mark.parametrize('unknown', [None, 1, 'true'])
def test_unknown_isolation_cannot_reach_agent(unknown):
    context = dict(phase='development', failure_class='assertion_failed',
                   isolation_ready=unknown, user_pause_requested=False,
                   evidence_matches_current_version=True, repair_budget_available=True,
                   execution_budget_available=True, needs_new_repair=True,
                   next_action_requires_execution_budget=True, output_agent=None)
    engine = DecisionEngine(read_json(Path(__file__).resolve().parents[1] / 'configs/decision_rules.json'))
    result = engine.decide(context, run_id='fixed-fixture', code_version='fixture', evidence_ids=[], decision_id='d')
    assert result['action'] == 'pause'
    assert result['target_agent'] is None
    assert result['failure_class'] == 'configuration_error'


@pytest.mark.parametrize('name', ['', '.', 'C:/outside.py', 'C:outside.py'])
def test_invalid_relative_paths_are_rejected(name):
    with pytest.raises(PermissionViolation):
        normalise_relpath(name)


def test_in_root_symlink_is_rejected_before_resolution(tmp_path):
    (tmp_path / 'real.py').write_text('x = 1\n', encoding='utf-8')
    link = tmp_path / 'allowed.py'
    try:
        link.symlink_to(tmp_path / 'real.py')
    except OSError:
        pytest.skip('Host does not permit creating symlinks; verify on target host')
    with pytest.raises(PermissionViolation):
        validate_changes([{'path': 'allowed.py', 'new_content': 'x = 2\n'}],
                         allowed_paths=('allowed.py',), forbidden_paths=('tests/',), work_root=tmp_path)
