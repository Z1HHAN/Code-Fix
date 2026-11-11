from dataclasses import replace
import json
from pathlib import Path
import shutil
import time

import httpx
import pytest

from app.live_model import Ledger, LiveModel, ModelBlocked
from app.personal import configure_runtime
from app.providers import validate_url
from app.role_config import RoleConfig
from app.model_profiles import ModelRouter
from app.remote_runner import RemoteRunner
from app.docker_runner import IsolationBlocked
from scripts.prepare_desktop import prepare_backend


@pytest.fixture
def personal(tmp_path, monkeypatch):
    root = tmp_path / 'app'
    prepare_backend(root)
    values = {'base_url': 'https://personal.example/v1', 'model': 'user-model',
              'max_output_tokens': 2048, 'token_parameter': 'max_tokens', 'price_version': 'personal-test'}
    for name, value in {
        'CODE_FIX_PERSONAL_MODE': '1', 'CODE_FIX_PERSONAL_BASE_URL': values['base_url'],
        'CODE_FIX_PERSONAL_KEY': 'test-no-real-key', 'CODE_FIX_PERSONAL_APPROVED': '1',
        'CODE_FIX_LIVE_APPROVED': '1', 'CODE_FIX_PERSONAL_BUDGET': 'usage',
    }.items():
        monkeypatch.setenv(name, value)
    configure_runtime(root, values)
    return root, values


def test_personal_bundle_is_empty_of_team_accounts_and_has_required_runtime_assets(tmp_path):
    root = tmp_path / 'bundle'
    prepare_backend(root)
    assert not (root / 'configs/model_profiles.json').exists()
    assert not (root / 'configs/agents.json').exists()
    assert 'sf_account' not in (root / 'configs/desktop-roles.json').read_text(encoding='utf-8')
    assert not (root / 'secrets').exists() and not (root / 'evidence').exists()
    for name in ('ui/app/index.html', 'prompts/repairer.md', 'configs/decision_rules.json', 'desktop-launch.py'):
        assert (root / name).is_file()


def test_personal_configuration_reaches_real_http_payload_without_team_keys(personal, tmp_path):
    root, values = personal
    config = RoleConfig(root)
    captured = []
    def handle(request):
        captured.append((str(request.url), request.headers['Authorization'], json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"status":"ok"}'}}],
                                        'usage': {'prompt_tokens': 5, 'completion_tokens': 8}})
    router = ModelRouter(config, Ledger(tmp_path / 'runs'),
                         transports={'primary': httpx.MockTransport(handle)})
    result = router.call_for_role('repairer', [{'role': 'user', 'content': 'hello'}],
                                 run_id='personal', call_id='p1', max_calls=2, deadline=time.monotonic()+10)
    assert result['status'] == 'ok'
    assert result['cost']['status'] == 'unpriced' and result['cost']['amount_usd'] is None
    assert captured == [(values['base_url']+'/chat/completions', 'Bearer test-no-real-key',
                         {'model': 'user-model', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 2048})]
    assert router.ledger.summary('personal')['amount_usd'] is None
    assert router.ledger.summary('personal')['status'] == 'unpriced'
    assert 'test-no-real-key' not in json.dumps(router.binding('repairer'))


def test_usage_budget_preserves_call_cap_and_unknown_blocks_mode_switch(personal, tmp_path):
    root, _ = personal
    settings = ModelRouter.resolve(RoleConfig(root))[0]['primary']
    ledger = Ledger(tmp_path / 'ledger')
    ledger.reserve('c1', 'r1', settings, 1, 100)
    ledger.settle('c1', {'prompt_tokens': 5, 'completion_tokens': 7}, settings)
    with pytest.raises(ModelBlocked, match='调用上限'):
        ledger.reserve('c2', 'r1', settings, 1, 100)
    ledger.reserve('c3', 'r2', settings, 2, 100)
    ledger.settle('c3', None, settings)
    with pytest.raises(ModelBlocked, match='未知'):
        ledger.reserve('c4', 'r3', settings, 2, 100)


@pytest.mark.parametrize('address', ['http://127.0.0.1:17880', 'http://localhost:17880', 'http://8.148.85.237:8780'])
def test_remote_http_requires_explicit_literal_loopback_tunnel(monkeypatch, address):
    monkeypatch.delenv('CODE_FIX_EXECUTION_SSH_TUNNEL', raising=False)
    with pytest.raises(IsolationBlocked):
        RemoteRunner(url=address, token='x'*40)
    monkeypatch.setenv('CODE_FIX_EXECUTION_SSH_TUNNEL', '1')
    if address == 'http://127.0.0.1:17880':
        runner = RemoteRunner(url=address, token='x'*40)
        runner.close()
    else:
        with pytest.raises(IsolationBlocked):
            RemoteRunner(url=address, token='x'*40)


def test_personal_provider_cannot_be_used_outside_explicit_owner_endpoint(personal, monkeypatch):
    assert validate_url('personal', 'https://personal.example/v1')
    assert not validate_url('personal', 'https://personal.example/other')
    assert not validate_url('personal', 'https://other.example/v1')
    monkeypatch.delenv('CODE_FIX_PERSONAL_MODE')
    assert not validate_url('personal', 'https://personal.example/v1')
