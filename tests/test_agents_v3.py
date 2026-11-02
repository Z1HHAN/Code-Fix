from decimal import Decimal
from io import BytesIO
import json
import time

import pytest

from app.agents_v3 import parse_role
from app.live_model import Ledger, LiveModel, LiveSettings, ModelBlocked


def settings(**overrides):
    values = dict(api_key='private-test-key', model='test-only-model', base_url='https://api.invalid/v1',
                  timeout=2, daily_limit=Decimal('.1'), input_per_million=Decimal('1'),
                  output_per_million=Decimal('2'))
    return LiveSettings(**(values | overrides))


def test_live_requires_explicit_approval_and_complete_budget(monkeypatch):
    monkeypatch.delenv('CODE_FIX_LIVE_APPROVED', raising=False)
    with pytest.raises(ModelBlocked):
        LiveSettings.from_env()
    monkeypatch.setenv('CODE_FIX_LIVE_APPROVED', '1')
    monkeypatch.setenv('CODE_FIX_API_KEY', 'test-key')
    monkeypatch.setenv('CODE_FIX_MODEL', 'test-model')
    monkeypatch.delenv('CODE_FIX_REQUEST_SECONDS', raising=False)
    with pytest.raises(ModelBlocked):
        LiveSettings.from_env()


def test_shared_ledger_reserves_before_call_and_blocks_unknown_cost(tmp_path):
    ledger = Ledger(tmp_path)
    s = settings()
    ledger.reserve('a', 'r', s, 2, 100)
    with pytest.raises(ModelBlocked):
        Ledger(tmp_path).reserve('b', 'other-run', s, 2, 100)
    assert ledger.settle('a', None, s)['status'] == 'unknown'
    with pytest.raises(ModelBlocked):
        Ledger(tmp_path).reserve('b', 'r', s, 2, 100)


def test_known_usage_is_persistent_and_call_count_enforced(tmp_path):
    ledger, s = Ledger(tmp_path), settings()
    ledger.reserve('a', 'r', s, 1, 100)
    assert ledger.settle('a', {'prompt_tokens': 20, 'completion_tokens': 10}, s)['amount_usd'] == '0.00004'
    with pytest.raises(ModelBlocked):
        Ledger(tmp_path).reserve('b', 'r', s, 1, 100)
    with pytest.raises(ModelBlocked):
        ledger.reserve('c', 'other', settings(daily_limit=Decimal('.000001')), 1, 100)


def test_http_protocol_is_offline_stub_and_secret_redacted(tmp_path, monkeypatch):
    import app.live_model as module
    seen = {}
    class OfflineOpener:
        def open(self, request, timeout):
            seen.update(url=request.full_url, body=json.loads(request.data), timeout=timeout)
            return BytesIO(json.dumps({'id': 'offline-response', 'choices': [{'message': {'content': 'private-test-key'}}],
                                       'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}).encode())
    monkeypatch.setattr(module, 'build_opener', lambda *args: OfflineOpener())
    model = LiveModel(settings(), Ledger(tmp_path))
    result = model.call([{'role': 'user', 'content': 'test'}], run_id='r', call_id='c', max_calls=1, deadline=time.monotonic()+10)
    assert result['status'] == 'ok' and result['raw_text'] == '[REDACTED]'
    assert seen['body']['max_completion_tokens'] == 2048
    assert seen['url'].endswith('/chat/completions')
    assert 'private-test-key' not in json.dumps(result)


def test_detector_and_scheduler_cannot_invent_citations():
    data = {'action': 'repair', 'reason': 'assertion', 'evidence_ids': ['e1'], 'knowledge_citations': []}
    assert parse_role('scheduler', json.dumps(data), ['e1'], [])['action'] == 'repair'
    with pytest.raises(ValueError):
        parse_role('scheduler', json.dumps(data | {'evidence_ids': ['not-provided']}), ['e1'], [])
    with pytest.raises(ValueError):
        parse_role('scheduler', json.dumps(data | {'action': 'success'}), ['e1'], [])
    with pytest.raises(ValueError):
        parse_role('scheduler', json.dumps(data | {'evidence_ids': []}), ['e1'], [])
    with pytest.raises(ValueError):
        parse_role('scheduler', json.dumps(data | {'action': []}), ['e1'], [])
