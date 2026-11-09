"""Approved strategy/configuration contract; all model/tool execution is offline."""
from copy import deepcopy
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents_v3 import AgentGateway
from app.api import create_app
from app.configuration import ConfigurationStore
from app.errors import InputValidationError
from app.live_model import Ledger
from app.model_profiles import ModelRouter
from app.role_config import digest
from app.run_store import RunStore
from app.tasks_v3 import get_task
from test_configuration import draft
from test_siliconflow_budget import enable
from test_workflow_v3 import ModelStub, execute


@pytest.mark.parametrize('failure', ['syntax_error', 'assertion_failed'])
def test_fixed_repeats_analysis_without_locator(tmp_path, failure):
    status, result, runner, model, store, rid = execute(
        tmp_path, ['assertion_failed', failure, 'passed', 'passed'], {'strategy': 'fixed'})
    assert status == 'success'
    assert model.roles == ['repairer', 'detector', 'scheduler'] * 2
    events = store.snapshot(rid)['events']
    assert all(e['strategy'] == 'fixed' for e in events if e['event'] == 'decision')
    if failure == 'assertion_failed':
        assert any(e.get('program_decision', {}).get('action') == 'locate'
                   and e['effective_action'] == 'repair' and '固定策略' in e['override_reason'] for e in events)


@pytest.mark.parametrize('strategy', ['fixed', 'dynamic'])
@pytest.mark.parametrize('advice,status', [('pause', 'paused'), ('stop', 'stopped')])
def test_both_strategies_accept_conservative_advice(tmp_path, strategy, advice, status):
    outcome, result, runner, model, store, rid = execute(tmp_path, ['assertion_failed', 'passed'],
                                                {'strategy': strategy}, scheduler=advice)
    assert outcome == status and result['final_result'] == 'not_run'
    assert 'final_check' not in runner.phases
    assert any(e['event'] == 'decision' and e.get('model_advice', {}) and
               '模型要求保守停止' in (e.get('override_reason') or '') for e in store.snapshot(rid)['events'])


@pytest.mark.parametrize('strategy', ['fixed', 'dynamic'])
def test_both_strategies_prioritize_final_without_fabricating_advice(tmp_path, strategy):
    status, _, _, model, store, rid = execute(tmp_path, ['assertion_failed', 'passed', 'passed'],
                                              {'strategy': strategy, 'max_calls': 1})
    assert status == 'success' and model.roles == ['repairer']
    decisions = [e for e in store.snapshot(rid)['events'] if e['event'] == 'decision' and e['phase'] == 'development']
    assert decisions[0]['model_advice'] is None and '跳过' in decisions[0]['override_reason']


@pytest.mark.parametrize('strategy', ['fixed', 'dynamic'])
def test_common_environment_guard_precedes_advisory_calls(tmp_path, strategy):
    status, _, _, model, *_ = execute(tmp_path, ['assertion_failed', 'environment_error'], {'strategy': strategy})
    assert status == 'paused' and model.roles == ['repairer']


def test_saved_strategy_is_authoritative_and_conflicts_rejected(tmp_path):
    store = RunStore(tmp_path)
    saved = ConfigurationStore(store).save(draft() | {'strategy': 'fixed'})
    payload = {'mode': 'repair', 'config_id': saved['config_id'], **saved['parameters']}
    assert store.create(payload)['task']['strategy'] == 'fixed'
    with pytest.raises(InputValidationError, match='策略.*不一致'):
        store.create(payload | {'strategy': 'dynamic'})
    with pytest.raises(InputValidationError):
        store.create(payload | {'mode': 'baseline'})
    assert store.create({'mode': 'repair'})['task']['strategy'] == 'dynamic'


@pytest.mark.parametrize('override', [
    {'task_instruction': 'x' * 4001}, {'task_instruction': None}, {'system_prompt': 'x'},
    {'generation': {'temperature': True}}, {'generation': {'top_p': 0}},
    {'generation': {'max_output_tokens': 999999}}, {'generation': {'max_output_tokens': 1.5}},
    {'generation': {'max_output_tokens': 10 ** 500}}, {'generation': {'temperature': float('nan')}},
])
def test_invalid_role_overrides_rejected(tmp_path, override):
    with pytest.raises(InputValidationError):
        ConfigurationStore(RunStore(tmp_path)).save(draft() | {'role_overrides': {'repairer': override}})


def test_unreviewed_sampling_is_rejected_not_ignored(tmp_path):
    with pytest.raises(InputValidationError, match='不受支持'):
        ConfigurationStore(RunStore(tmp_path)).save(draft() | {
            'role_overrides': {'detector': {'generation': {'temperature': .5}}}})


def test_saved_config_wire_request_and_call_evidence_match_per_role(tmp_path, monkeypatch):
    enable(monkeypatch)
    store = RunStore(tmp_path)
    configs = ConfigurationStore(store)
    value = draft()
    value['bindings'] = {role: 'sf_account1_v32' for role in value['bindings']}
    value['role_overrides'] = {role: {'task_instruction': '独立指令 ' + role,
                                        'generation': {'max_output_tokens': 200 + i, 'temperature': .1 * i, 'top_p': .7}}
                               for i, role in enumerate(value['bindings'])}
    preview, saved = configs.validate(value), configs.save(value)
    runtime = configs.runtime(saved['config_id'])
    initial_resolved = ModelRouter.resolve(runtime)
    captures = []
    stub = ModelStub()
    def respond(request):
        payload = json.loads(request.content)
        captures.append(payload)
        output = stub.call(payload['messages'])['raw_text']
        return httpx.Response(200, json={'choices': [{'message': {'content': output}}],
                                        'usage': {'prompt_tokens': 1, 'completion_tokens': 2}})
    router = ModelRouter(runtime, Ledger(tmp_path), initial_resolved,
                         transports={r: httpx.MockTransport(respond) for r in value['bindings']})
    rid = store.create({'mode': 'repair', 'config_id': saved['config_id'], **saved['parameters']})['run_id']
    store.claim(rid)
    definition = get_task('leap')
    base = definition.prepare(store.directory(rid) / 'base')
    from app.runner import code_version
    version = code_version(base, (definition.source,))
    ev = {'evidence_id': 'dev', 'phase': 'development', 'code_version': version,
          'failure_class': 'assertion_failed', 'summary': 'offline'}
    for role in value['bindings']:
        record = AgentGateway(router, store, runtime).invoke(role, run_id=rid, round_number=1,
            code=(base / definition.source).read_text(encoding='utf-8'), evidence=ev, version=version,
            work=base, task=store.get(rid)['task'], deadline=time.monotonic() + 5)
        assert record['status'] == 'ok' and not record['model_api_called']
        expected = preview['effective_roles'][role]
        wire = captures[-1]
        assert {k: v for k, v in wire.items() if k not in {'model', 'messages'}} == expected['parameters']
        material = json.loads(wire['messages'][1]['content'])
        assert material['role_task_instruction'] == expected['task_instruction']
        assert wire['messages'][0]['content'] == runtime.prompts[role][0]
        evidence = json.loads((store.directory(rid) / 'calls' / record['call_id'] / 'input.json').read_text(encoding='utf-8'))
        assert evidence['messages'] == wire['messages']
        assert evidence['provenance']['messages_sha256'] == digest(wire['messages'])
        assert record['provenance']['model_binding']['effective_parameters'] == expected['parameters']
        assert router.clients[role].settings.billable_output_tokens == initial_resolved[0][role].billable_output_tokens
    assert len({c['max_tokens'] for c in captures}) == 4
    assert all(s.max_output_tokens == 2048 for s in initial_resolved[0].values())
    assert configs.get(saved['config_id']) == saved


def test_large_config_body_and_chunked_limits_keep_task_limit(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        headers = {'X-Codefix-Token': client.get('/api/v3/session').json()['token']}
        value = draft() | {'role_overrides': {r: {'task_instruction': '指' * 4000} for r in draft()['bindings']}}
        body = json.dumps(value, ensure_ascii=False).encode()
        assert 8192 < len(body) < 131072
        assert client.post('/api/v3/configurations', headers=headers, content=body).status_code == 201
        assert client.post('/api/v3/tasks', headers=headers, content=body).status_code == 413
        assert client.post('/api/v3/configurations', headers=headers, content=iter([b' ' * 70000] * 2)).status_code == 413
        assert client.post('/api/v3/tasks', headers=headers, content=iter([b' ' * 5000] * 2)).status_code == 413


def test_existing_snapshot_is_unchanged_by_new_override_configuration(tmp_path):
    configs = ConfigurationStore(RunStore(tmp_path))
    old = configs.save(draft())
    old_bytes = json.dumps(old, sort_keys=True)
    new = configs.save(draft() | {'role_overrides': {'repairer': {'task_instruction': 'new instruction'}}})
    assert old['config_id'] != new['config_id']
    assert 'role_overrides' not in old
    assert json.dumps(configs.get(old['config_id']), sort_keys=True) == old_bytes
    assert configs.runtime(old['config_id']).role_overrides == {}
