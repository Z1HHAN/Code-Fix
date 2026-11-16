"""Offline agent collaboration and actual branch checks; no paid requests."""
import json
from copy import deepcopy

import pytest

from app.agent_team import create_team
from app.repair_output import InvalidModelOutput
from app.role_config import digest
from test_chain_retries import execute
from test_workflow_v3 import ModelStub


class RecordingModel(ModelStub):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.materials = []

    def call(self, messages, **kwargs):
        self.materials.append(json.loads(messages[1]['content']))
        return super().call(messages, **kwargs)


def test_four_agents_handoff_current_evidence_and_resume_loop(tmp_path):
    model = RecordingModel()
    status, result, runner, store, rid = execute(tmp_path, model,
        ['assertion_failed', 'assertion_failed', 'passed', 'passed'])
    assert status == 'success' and result['rounds_used'] == 2
    assert model.roles == ['repairer', 'detector', 'scheduler', 'locator', 'repairer', 'detector']
    assert runner.phases == ['development', 'development', 'development', 'final_check']
    assert [[h['agent_id'] for h in m['upstream_results']] for m in model.materials] == [
        [], [], ['detector'], ['detector', 'scheduler'], ['detector', 'scheduler', 'locator'], []]
    folder = store.directory(rid)
    team = json.loads((folder / 'trace/agent-team.json').read_text(encoding='utf-8'))
    assert len({x['agent_instance_id'] for x in team['agents'].values()}) == 4
    assert len(create_team()) == 4
    assert team['final_feedback_to_agents'] is False
    calls = [e for e in store.events(rid) if e['event'] == 'agent_call']
    assert len({e['call_id'] for e in calls}) == 6
    for call in calls:
        p = call['provenance']
        assert p['agent_contract_sha256'] == digest(p['agent_contract'])
        assert p['agent_instance_id'] == rid + ':' + call['role']
        assert p['agent_contract']['can_approve_success'] is False
        for h in p['handoffs']:
            original = json.loads((folder / h['artifact_path'] / 'output.json').read_text(encoding='utf-8'))
            assert h['output_sha256'] == digest(original['parsed_output'])
            assert h['code_version'] == call['code_version']
    loops = [e for e in store.events(rid) if e['event'] == 'decision' and e.get('self_loop')]
    assert len(loops) == 1 and loops[0]['self_loop']['via'] == ['locator', 'repairer']
    resumed = [e for e in store.events(rid) if e['event'] == 'self_loop_resumed']
    assert len(resumed) == 1 and resumed[0]['round'] == 2
    assert resumed[0]['trigger_evidence_id'] == 'test-development-1'
    assert 'test-final-check' not in json.dumps(model.materials)


def test_old_agent_outputs_do_not_leak_into_new_candidate(tmp_path):
    model = RecordingModel()
    status, _, _, _, _ = execute(tmp_path, model,
        ['assertion_failed', 'assertion_failed', 'assertion_failed', 'passed', 'passed'])
    assert status == 'success'
    detectors = [m for m in model.materials if m['agent_contract']['agent_id'] == 'detector']
    assert len(detectors) == 3 and all(not m['upstream_results'] for m in detectors)
    for m in model.materials:
        assert all(h['evidence_id'] == m['dev_evidence'][0]['evidence_id'] for h in m['upstream_results'])


@pytest.mark.parametrize('change', ['role', 'version', 'evidence', 'hash', 'duplicate'])
def test_agent_rejects_misrouted_or_stale_handoff(change):
    h = {'agent_id': 'detector', 'code_version': 'v', 'evidence_id': 'e',
         'call_id': 'm-detect', 'step_id': 's-detect', 'artifact_path': 'calls/m-detect',
         'output': {'analysis': 'current'}, 'output_sha256': digest({'analysis': 'current'})}
    handoffs = [deepcopy(h)]
    if change == 'role': handoffs[0]['agent_id'] = 'repairer'
    if change == 'version': handoffs[0]['code_version'] = 'old'
    if change == 'evidence': handoffs[0]['evidence_id'] = 'test-final-check'
    if change == 'hash': handoffs[0]['output']['analysis'] = 'tampered'
    if change == 'duplicate': handoffs.append(deepcopy(h))
    with pytest.raises(InvalidModelOutput, match='交接'):
        create_team()['scheduler'].inputs(handoffs, {'evidence_id': 'e'}, 'v')


@pytest.mark.parametrize('strategy,outcome,roles,loop_path', [
    ('dynamic', 'syntax_error', ['repairer', 'detector', 'repairer', 'detector'], ['repairer']),
    ('fixed', 'assertion_failed', ['repairer', 'detector', 'scheduler', 'repairer', 'detector'], ['repairer']),
    ('dynamic', 'code_runtime_error', ['repairer', 'detector', 'scheduler', 'locator', 'repairer', 'detector'], ['locator', 'repairer']),
])
def test_evidence_selects_roles_not_a_mandatory_four_agent_pipeline(tmp_path, strategy, outcome, roles, loop_path):
    model = RecordingModel()
    status, _, _, store, rid = execute(tmp_path, model,
        ['assertion_failed', outcome, 'passed', 'passed'], strategy=strategy)
    assert status == 'success' and model.roles == roles
    loops = [e['self_loop'] for e in store.events(rid) if e['event'] == 'decision' and e.get('self_loop')]
    assert len(loops) == 1 and loops[0]['via'] == loop_path


@pytest.mark.parametrize('task,scheduler', [({'max_rounds': 1}, 'repair'), ({}, 'stop'), ({'max_calls': 1}, 'repair')])
def test_limits_and_conservative_advice_do_not_resume_loop(tmp_path, task, scheduler):
    model = RecordingModel(scheduler=scheduler)
    status, _, _, store, rid = execute(tmp_path, model,
        ['assertion_failed', 'assertion_failed'], **task)
    assert status in {'stopped', 'blocked'}
    assert not any(e['event'] == 'self_loop_resumed' for e in store.events(rid))
    assert json.loads((store.directory(rid) / 'handoff.json').read_text(encoding='utf-8'))['handling_status'] == 'pending_human'


@pytest.mark.parametrize('missing', ['detector', 'scheduler', 'dispatch_instruction'])
def test_locator_missing_dependency_blocks_before_request(tmp_path, monkeypatch, missing):
    from app.agent_team import RoleAgent
    original = RoleAgent.inputs
    def lose_dependency(self, handoffs, evidence, version):
        if self.role == 'locator':
            handoffs = deepcopy(handoffs)
            if missing == 'dispatch_instruction':
                for item in handoffs: item.pop('dispatch_instruction', None)
            else:
                handoffs = [h for h in handoffs if h['agent_id'] != missing]
        return original(self, handoffs, evidence, version)
    monkeypatch.setattr(RoleAgent, 'inputs', lose_dependency)
    model = RecordingModel()
    status, result, runner, store, rid = execute(tmp_path, model, ['assertion_failed', 'assertion_failed'])
    assert status == 'blocked' and result['failure_class'] == 'agent_handoff_blocked'
    assert model.roles == ['repairer', 'detector', 'scheduler']
    assert 'locator' not in model.roles and 'final_check' not in runner.phases
    assert any(e['event'] == 'step_completed' and e.get('failure_class') == 'agent_handoff_blocked'
               for e in store.events(rid))


def test_detector_cannot_turn_failed_tool_test_into_pass(tmp_path):
    class LyingDetector(RecordingModel):
        def call(self, messages, **kwargs):
            record = super().call(messages, **kwargs)
            if self.roles[-1] == 'detector':
                output = json.loads(record['raw_text'])
                output['failure_class'] = 'passed'
                record['raw_text'] = json.dumps(output)
            return record
    model = LyingDetector()
    status, result, runner, *_ = execute(tmp_path, model, ['assertion_failed', 'assertion_failed'])
    assert status == 'blocked' and result['final_result'] == 'not_run'
    assert model.roles == ['repairer', 'detector', 'detector']
    assert 'final_check' not in runner.phases


def test_passing_candidate_is_not_finalized_without_required_detector_budget(tmp_path):
    model = RecordingModel()
    status, result, runner, *_ = execute(tmp_path, model, ['assertion_failed', 'passed'], max_calls=1)
    assert status == 'blocked' and result['dev_result'] == 'passed'
    assert result['final_result'] == 'not_run' and model.roles == ['repairer']
    assert '调用次数' in result['reason'] and 'final_check' not in runner.phases
