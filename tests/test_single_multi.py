import json
import pytest
from app.comparisons import ComparisonStore, export_csv
from app.run_store import RunStore
from app.role_config import digest
from app.experiment_identity import code_identity
from app.config import ConfigError
from test_workflow_v3 import execute


@pytest.mark.parametrize('failure', ['assertion_failed', 'syntax_error', 'code_runtime_error'])
def test_single_agent_uses_only_repairer_and_shared_tool_final(tmp_path, failure):
    status, result, runner, model, store, rid = execute(tmp_path,
        ['assertion_failed', failure, 'passed', 'passed'], {'strategy': 'single'})
    assert status == 'success' and model.roles == ['repairer', 'repairer']
    assert runner.phases == ['development', 'development', 'development', 'final_check']
    assert result['rounds_used'] == 2
    team = json.loads((store.directory(rid) / 'trace/agent-team.json').read_text())
    assert team['active_agents'] == ['repairer']


def test_agent_comparison_matches_same_inputs_and_keeps_export(tmp_path):
    ids = {s: execute(tmp_path, ['assertion_failed', 'passed', 'passed'], {'strategy': s})[-1]
           for s in ('single', 'dynamic')}
    store = RunStore(tmp_path)
    data = ComparisonStore(store).create({'kind': 'agents', 'title': 'same budget', 'scope': 'offline',
        'pairs': [{'project': 'leap', **ids, 'not_run_reason': {}}]})
    assert data['summary']['single']['requests'] == data['summary']['dynamic']['requests'] == 0
    assert data['pairs'][0]['single']['role_calls'] == 1
    assert data['pairs'][0]['dynamic']['role_calls'] == 2
    assert data['paired_outcomes']['both_success'] == 1
    assert data['arms'] == ['single', 'dynamic']
    assert 'single' in export_csv(data) and 'dynamic' in export_csv(data)
    assert ComparisonStore(store).get(data['comparison_id']) == data


def test_single_agent_does_not_bypass_environment_or_round_limit(tmp_path):
    status, result, runner, model, *_ = execute(tmp_path / 'environment',
        ['assertion_failed', 'environment_error'], {'strategy': 'single'})
    assert status == 'paused' and model.roles == ['repairer'] and 'final_check' not in runner.phases
    status, result, runner, model, *_ = execute(tmp_path / 'limit',
        ['assertion_failed', 'assertion_failed'], {'strategy': 'single', 'max_rounds': 1})
    assert status == 'stopped' and result['rounds_used'] == 1
    assert model.roles == ['repairer'] and result['final_result'] == 'not_run'


def test_manifest_identity_verifies_actual_bytes_without_git(tmp_path, monkeypatch):
    (tmp_path / 'app').mkdir();(tmp_path / 'app/x.py').write_bytes(b'x=1\n')
    import hashlib
    files = {'app/x.py': hashlib.sha256(b'x=1\n').hexdigest()}
    (tmp_path / 'codefix-runtime-source.json').write_text(json.dumps({'commit': 'f'*40, 'files': files, 'source_sha256': digest(files)}))
    monkeypatch.setattr('subprocess.run', lambda *a, **k: pytest.fail('verified bundle should not run git'))
    assert code_identity(tmp_path)['verified_bundle'] is True
    (tmp_path / 'app/x.py').write_bytes(b'x=2\n')
    with pytest.raises(ConfigError, match='冻结清单'):
        code_identity(tmp_path)
