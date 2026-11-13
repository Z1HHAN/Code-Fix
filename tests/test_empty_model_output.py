"""Known empty responses retry; unknown costs and HTTP failures do not."""
import time
from decimal import Decimal

import httpx
import pytest

from app.live_model import Ledger, LiveModel, LiveSettings
from test_chain_retries import execute
from test_workflow_v3 import ModelStub


def empty_model(root, *, unknown=False, finish_reason='length'):
    def response(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': finish_reason,
            'message': {'content': ''}}], 'usage': None if unknown else {
                'prompt_tokens': 10, 'completion_tokens': 2048,
                'completion_tokens_details': {'reasoning_tokens': 2048, 'text_tokens': 0}}})
    settings = LiveSettings('fixture-key', 'fixture-model', 'https://example.com/v1',
        10, Decimal('5'), Decimal('1'), Decimal('2'), max_output_tokens=2048)
    return LiveModel(settings, Ledger(root), transport=httpx.MockTransport(response))


def test_empty_response_is_diagnosed_and_keeps_known_cost(tmp_path):
    model = empty_model(tmp_path)
    record = model.call([{'role': 'user', 'content': 'fixture'}], run_id='r', call_id='c',
                        max_calls=3, deadline=time.monotonic()+10)
    assert record['failure_class'] == 'invalid_model_output'
    assert record['error_type'] == 'EmptyModelOutput'
    assert record['finish_reason'] == 'length'
    assert record['cost']['status'] == 'estimated'
    assert record['output_diagnostics']['kind'] == 'reasoning_budget_exhausted'
    assert record['output_diagnostics']['empty_content'] is True
    assert record['output_diagnostics']['max_output_tokens'] == 2048
    assert record['output_diagnostics']['reasoning_tokens'] == 2048
    assert record['output_diagnostics']['text_tokens'] == 0
    assert '没有返回正文' in record['reason'] and '2048' in record['reason']


def test_empty_response_with_unknown_usage_stops_before_format_retry(tmp_path):
    model = empty_model(tmp_path, unknown=True)
    record = model.call([{'role': 'user', 'content': 'fixture'}], run_id='r', call_id='c',
                        max_calls=3, deadline=time.monotonic()+10)
    assert record['failure_class'] == 'cost_unknown' and record['status'] == 'blocked'
    assert record['cost']['status'] == 'unknown'


@pytest.mark.parametrize('known_usage', [True, False])
def test_truncated_body_is_not_applied_and_unknown_cost_has_priority(tmp_path, known_usage):
    model = empty_model(tmp_path)
    model.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices': [{'finish_reason': 'length', 'message': {'content': '{"changes":['}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 2048} if known_usage else None}))
    record = model.call([], run_id='r', call_id='c', max_calls=3, deadline=time.monotonic()+10)
    assert record['status'] != 'ok'
    assert record['failure_class'] == ('invalid_model_output' if known_usage else 'cost_unknown')
    if known_usage:
        assert record['raw_text'] == '{"changes":['
        assert record['output_diagnostics']['kind'] == 'truncated_output'
        assert record['error_type'] == 'TruncatedModelOutput'


@pytest.mark.parametrize('empty_count,expected,rounds', [(1, 'success', 2), (2, 'blocked', 2), (3, 'blocked', 2)])
def test_known_empty_repair_responses_consume_existing_rounds(tmp_path, empty_count, expected, rounds):
    class EmptyFirst(ModelStub):
        attempts = 0
        def call(self, messages, **kwargs):
            self.attempts += 1
            if self.attempts <= empty_count:
                return empty_model(tmp_path/'ledger').call(messages, **kwargs)
            if 'V3检测员' not in messages[0]['content']:
                assert '没有返回正文' in messages[1]['content']
            return super().call(messages, **kwargs)
    status, result, runner, store, rid = execute(tmp_path/'task', EmptyFirst(),
        ['assertion_failed', 'passed', 'passed'])
    assert status == expected and result['rounds_used'] == rounds
    assert result['model_attempts'] == rounds + (1 if expected == 'success' else 0)
    assert runner.phases == (['development'] if expected != 'success' else ['development', 'development', 'final_check'])
    calls = [e for e in store.events(rid) if e['event'] == 'agent_call']
    assert all(c['cost']['status'] == 'estimated' for c in calls[:empty_count])
    from app.service import TaskService
    store.finish_if_running(rid, status, result)
    summary = TaskService(store.root).report(rid)['result_summary']
    assert len(summary['model_output_issues']) == min(empty_count, 2)
    assert all(issue['diagnostics']['kind'] == 'reasoning_budget_exhausted'
               for issue in summary['model_output_issues'])
