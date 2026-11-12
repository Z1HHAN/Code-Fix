"""Approved recovery rules; synthetic model and executor, no paid requests."""
import json
import pytest
from app.role_overrides import capabilities, validate_overrides
from app.errors import InputValidationError
from test_chain_retries import execute
from test_workflow_v3 import ModelStub


@pytest.mark.parametrize('count', [1, 2])
def test_invalid_locator_corrects_once_then_falls_back_to_current_evidence(tmp_path, count):
    class LocatorFailure(ModelStub):
        attempts = 0
        def call(self, messages, **kwargs):
            if '定位员提示词' in messages[0]['content']:
                self.attempts += 1
                if self.attempts <= count:
                    return {'status': 'ok', 'raw_text': '{}', 'model_api_called': False}
            return super().call(messages, **kwargs)
    model = LocatorFailure(scheduler='locate')
    status, result, runner, store, rid = execute(tmp_path, model,
        ['assertion_failed', 'assertion_failed', 'passed', 'passed'])
    assert status == 'success' and model.attempts == 2
    events = store.events(rid)
    assert len([e for e in events if e['event'] == 'format_retry' and e['role'] == 'locator']) == 1
    assert len([e for e in events if e['event'] == 'locator_fallback']) == (1 if count == 2 else 0)
    assert runner.phases == ['development', 'development', 'development', 'final_check']


@pytest.mark.parametrize('host,supported', [('maas.qianwenaiapi.com', True), ('other.example', False)])
def test_thinking_controls_are_endpoint_and_model_specific(host, supported):
    p = {'provider_id': 'personal', 'base_url': 'https://' + host + '/v1',
         'model': 'deepseek-v4.1-flash-1017', 'max_output_tokens': 8192, 'token_limit_parameter': 'max_tokens'}
    assert ('enable_thinking' in capabilities(p)) == supported
    registry = {'profiles': {'p': p}}
    override = {'repairer': {'generation': {'enable_thinking': False}}}
    if supported:
        assert validate_overrides(override, {'repairer': 'p'}, registry) == {
            'repairer': {'task_instruction': '', 'generation': {'enable_thinking': False}}}
        for generation in [{'enable_thinking': 'false'}, {'reasoning_effort': 'none'},
                           {'enable_thinking': False, 'reasoning_effort': 'low'}]:
            with pytest.raises(InputValidationError):
                validate_overrides({'repairer': {'generation': generation}}, {'repairer': 'p'}, registry)
    else:
        with pytest.raises(InputValidationError):
            validate_overrides(override, {'repairer': 'p'}, registry)
