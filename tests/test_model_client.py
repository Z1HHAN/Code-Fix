import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.model_client import CallBudget, LiveCallsBlocked, MockTransport, ModelClient, OpenAICompatibleTransport, TransportError
from app.model_context import build_model_context

ROOT = Path(__file__).resolve().parents[1]


def context(tmp_path, role='repairer'):
    agents = json.loads((ROOT / 'configs/agents.json').read_text(encoding='utf-8'))
    return build_model_context(
        project_root=ROOT, agents_config=agents, role=role,
        task={'task_id': 'fixture', 'description': 'fixture only', 'allowed_paths': ['code/a.py']},
        code={'code/a.py': 'x = 0\n'},
        evidence=[{'evidence_id': 'e1', 'phase': 'development', 'code_version': 'v1', 'summary': 'assertion failed'}],
        knowledge=[{'citation_id': 'k1', 'knowledge_id': 'business_rules', 'source_path': 'knowledge/demo_project/business_rules.md', 'excerpt': 'fixture'}],
        work_root=tmp_path, code_version='v1',
    )


def repair(**updates):
    value = {'changes': [{'path': 'code/a.py', 'new_content': 'x = 1\n'}],
             'explanation': 'fixture', 'evidence_ids': ['e1'], 'knowledge_citations': ['k1']}
    return json.dumps(value | updates)


def test_mock_valid_output_does_not_apply_candidate(tmp_path):
    result = ModelClient(MockTransport([repair()]), CallBudget(2, 1)).invoke(context(tmp_path), run_id='r', call_id='c')
    assert result['status'] == 'ok' and result['mode'] == 'mock'
    assert result['model_api_called'] is False
    assert result['parsed_output']['changes'][0]['new_content'] == 'x = 1\n'
    assert result['cost_status'] == 'not_applicable'
    assert result['usage'] == {'input_tokens': None, 'output_tokens': None}
    assert result['attempts'] == result['calls_used'] == 1
    assert not (tmp_path / 'code/a.py').exists()


def test_budget_shared_across_clients_and_failed_attempts(tmp_path):
    budget = CallBudget(2, 2)
    first = ModelClient(MockTransport([TransportError('secret must not enter record'), repair()]), budget)
    result = first.invoke(context(tmp_path), run_id='r', call_id='c1')
    assert result['status'] == 'ok' and result['attempts'] == 2 and result['calls_used'] == 2
    assert 'secret must not' not in json.dumps(result)
    blocked = ModelClient(MockTransport([repair()]), budget).invoke(context(tmp_path), run_id='r', call_id='c2')
    assert blocked['status'] == 'blocked' and blocked['attempts'] == 0


def test_transport_failure_preserves_finite_attempt_records(tmp_path):
    result = ModelClient(MockTransport([TransportError('fixture')] * 5), CallBudget(10, 2)).invoke(context(tmp_path), run_id='r', call_id='c')
    assert result['failure_class'] == 'model_transport_error'
    assert result['attempts'] == 2 and len(result['attempt_records']) == 2
    assert result['raw_text'] is None


@pytest.mark.parametrize('payload', [repair(evidence_ids=['unknown']), repair(knowledge_citations=['unknown']),
                                    repair(changes=[{'path': 'tests/test_a.py', 'new_content': 'x'}]),
                                    repair(changes=[{'path': '../escape.py', 'new_content': 'x'}]),
                                    repair(explanation=1), 'not json'])
def test_invalid_or_unauthorized_output_is_recorded_not_written(tmp_path, payload):
    result = ModelClient(MockTransport([payload]), CallBudget(1, 1)).invoke(context(tmp_path), run_id='r', call_id='c')
    assert result['status'] != 'ok'
    assert result['raw_text'] == payload
    assert result['parsed_output'] is None
    assert list(tmp_path.iterdir()) == []


def test_disabled_live_transport_does_not_import_sdk_or_spend_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_FIX_ENABLE_LIVE', 'true')
    transport = OpenAICompatibleTransport(api_key='fixture-not-real', model='existing-model', timeout_seconds=5)
    budget = CallBudget(2, 1)
    result = ModelClient(transport, budget).invoke(context(tmp_path), run_id='r', call_id='c')
    assert result['status'] == 'blocked' and result['attempts'] == budget.calls_used == 0
    assert result['model_api_called'] is False
    assert 'fixture-not-real' not in json.dumps(result)


@pytest.mark.parametrize('calls, attempts', [(0, 1), (True, 1), (1, 0), (1, -1)])
def test_invalid_budget_rejected(calls, attempts):
    with pytest.raises(ValueError):
        CallBudget(calls, attempts)


def test_sdk_adapter_options_and_normalization_are_offline():
    captured = {}
    def fake_sdk(**options):
        captured.update(options)
        return SimpleNamespace(name='fake')
    transport = OpenAICompatibleTransport(api_key='fixture', model='existing-model',
                                          base_url='https://fixture.invalid', timeout_seconds=7)
    assert transport.prepare_client(sdk_factory=fake_sdk).name == 'fake'
    assert captured == {'api_key': 'fixture', 'base_url': 'https://fixture.invalid', 'timeout': 7, 'max_retries': 0}
    request = transport.build_request(({'role': 'user', 'content': 'fixture'},))
    assert request['model'] == 'existing-model' and request['timeout'] == 7
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))],
                               usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2))
    normalized = transport.normalize_response(response)
    assert normalized['usage'] == {'input_tokens': 4, 'output_tokens': 2}
    assert normalized['cost_status'] == 'unknown' and normalized['cost_amount'] is None
    with pytest.raises(LiveCallsBlocked): transport.prepare_client()
    with pytest.raises(LiveCallsBlocked): transport.send(request['messages'])


def test_locator_output_validates_citations_and_allowed_file(tmp_path):
    value = {'file': 'code/a.py', 'function': 'f', 'hypothesis': 'fixture', 'evidence_ids': ['e1'], 'knowledge_citations': ['k1']}
    client = ModelClient(MockTransport([json.dumps(value), json.dumps(value | {'file': '../outside.py'})]), CallBudget(2, 1))
    assert client.invoke(context(tmp_path, 'locator'), run_id='r', call_id='c1')['status'] == 'ok'
    denied = client.invoke(context(tmp_path, 'locator'), run_id='r', call_id='c2')
    assert denied['failure_class'] == 'permission_violation'


def test_invalid_output_uses_one_attempt_without_transport_retry(tmp_path):
    result = ModelClient(MockTransport(['not json', repair()]), CallBudget(3, 3)).invoke(context(tmp_path), run_id='r', call_id='c')
    assert result['failure_class'] == 'invalid_model_output' and result['attempts'] == 1


def test_non_transport_exception_stops_and_redacts_message(tmp_path):
    result = ModelClient(MockTransport([ValueError('fixture-private-token'), repair()]), CallBudget(3, 3)).invoke(
        context(tmp_path), run_id='r', call_id='c')
    assert result['status'] == 'failed'
    assert result['failure_class'] == 'model_client_error'
    assert result['attempts'] == result['calls_used'] == 1
    assert result['attempt_records'][0]['error_type'] == 'ValueError'
    assert result['model_api_called'] is False
    assert 'fixture-private-token' not in json.dumps(result)


def test_oversized_json_integer_returns_failed_record(tmp_path):
    raw = '{"explanation":' + '1' * 5000 + '}'
    result = ModelClient(MockTransport([raw]), CallBudget(1, 1)).invoke(context(tmp_path), run_id='r', call_id='c')
    assert result['failure_class'] == 'invalid_model_output'
    assert result['attempts'] == 1
