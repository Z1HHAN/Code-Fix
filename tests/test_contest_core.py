"""Offline acceptance of stage A; no provider requests or candidate execution."""
import asyncio
from decimal import Decimal
import json
import hashlib
import io
from pathlib import Path
import shutil
import sqlite3
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import ConfigError
from app.live_model import Ledger, LiveModel, ModelBlocked
from app.model_profiles import ModelRouter, load_profiles
from app.role_config import RoleConfig
from app.run_store import RunStore
from app.service import TaskService
from app.workflow_v3 import Workflow
from test_agents_v3 import settings
from test_workflow_v3 import ExecutorStub, ModelStub, execute


@pytest.mark.parametrize('outcomes,task,advice,expected', [
    (['assertion_failed', 'passed', 'passed'], {}, 'repair', 'success'),
    (['assertion_failed', 'assertion_failed', 'passed', 'passed'], {}, 'final_check', 'success'),
    (['assertion_failed', 'syntax_error', 'passed', 'passed'], {}, 'locate', 'success'),
    (['assertion_failed', 'assertion_failed'], {'max_rounds': 1}, 'final_check', 'stopped'),
    (['assertion_failed', 'environment_error'], {}, 'repair', 'paused'),
    (['assertion_failed', 'passed'], {}, 'pause', 'paused'),
    (['assertion_failed', 'passed', 'assertion_failed'], {}, 'repair', 'failed'),
    (['assertion_failed', 'assertion_failed'], {'max_calls': 3}, 'repair', 'blocked'),
])
def test_actual_dispatch_and_every_exit_have_versioned_evidence(tmp_path, outcomes, task, advice, expected):
    status, result, runner, model, store, rid = execute(tmp_path, outcomes, task, advice)
    assert status == expected
    events = store.events(rid)
    starts = [e for e in events if e['event'] == 'step_started']
    completed = [e for e in events if e['event'] == 'step_completed']
    assert len(starts) == len(completed)
    assert len({e['step_id'] for e in starts}) == len(starts)
    assert [e['parent_step_id'] for e in starts] == [None] + [e['step_id'] for e in starts[:-1]]
    tests = {e['evidence_id']: e for e in events if e['event'] == 'test_completed'}
    decisions = [e for e in events if e['event'] == 'decision']
    assert decisions[-1]['effective_action'] in {'finish', 'stop', 'pause'}
    for event in decisions:
        record = json.loads((store.directory(rid) / event['artifact_path']).read_text(encoding='utf-8'))
        assert record['effective_action'] == event['action']
        assert record['target_agent'] == {'repair': 'repairer', 'locate': 'locator'}.get(event['action'])
        if event['model_advice']:
            assert record['failure_class'] == record['program_decision']['failure_class']
            assert record['retries_left'] == record['program_decision']['retries_left']
        assert record['test_suite_version'] == result['test_suite_version']
        assert record['counters']['cost']['amount_usd'] is None  # offline is not a price
        for eid in record['evidence_ids']:
            assert eid in tests
            assert (store.directory(rid) / tests[eid]['evidence_path']).is_dir()
    for event in (e for e in decisions if e['model_advice']):
        next_start = next(e for e in starts if e['parent_step_id'] == event['step_id'])
        actual = event['effective_action']
        if actual in {'repair', 'locate', 'final_check'}:
            assert next_start['node_id'] == {'repair': 'repairer', 'locate': 'locator', 'final_check': 'pytest'}[actual]
        if actual != event['model_advice']['action']:
            assert event['override_reason']
    if status == 'success':
        assert not (store.directory(rid) / 'handoff.json').exists()
    else:
        handoff = json.loads((store.directory(rid) / result['handoff_path']).read_text(encoding='utf-8'))
        assert handoff['handling_status'] == 'pending_human' and handoff['accepted_by'] is None
        assert handoff['automation_stopped'] and handoff['run_status'] == status
        assert handoff['code_version'] == result['code_version']
        assert (store.directory(rid) / handoff['code_path']).is_file()
        assert handoff['decision_paths'] and handoff['evidence_ids']
    # Repeated role executions remain separate immutable instances.
    repair_steps = [e for e in starts if e['node_id'] == 'repairer']
    assert len(repair_steps) == model.roles.count('repairer')


def test_invalid_json_stops_once_and_records_production_guard(tmp_path):
    class Invalid(ModelStub):
        def call(self, *args, **kwargs):
            return {'status': 'ok', 'mode': 'test_stub', 'model_api_called': False, 'raw_text': 'not JSON'}
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    runner = ExecutorStub(['assertion_failed'])
    status, result = Workflow(store, runner, Invalid()).run(rid)
    assert status == 'blocked' and result['model_calls'] == 1
    assert result['guard_id'] == 'V3_INVALID_MODEL_OUTPUT_STOP'
    assert not (store.directory(rid) / 'candidate-1').exists()
    assert [e for e in store.events(rid) if e['event'] == 'decision'][-1]['action'] == 'stop'
    assert json.loads((store.directory(rid) / 'trace/configuration.json').read_text(encoding='utf-8'))['production_guards']['invalid_model_output'] == 'stop'


@pytest.mark.parametrize('kind', ['code_version', 'test_suite_version', 'exception'])
def test_rejected_test_evidence_or_exception_never_reaches_agents(tmp_path, kind):
    class Broken(ExecutorStub):
        def run(self, *args, **kwargs):
            if kind == 'exception':
                raise RuntimeError('sensitive exception text must not be recorded')
            return super().run(*args, **kwargs) | {kind: 'incorrect-version'}
    store = RunStore(tmp_path)
    rid = store.create({'mode': 'repair'})['run_id']
    store.claim(rid)
    model = ModelStub()
    status, result = Workflow(store, Broken(['assertion_failed']), model).run(rid)
    assert status in {'blocked', 'failed'} and not model.roles
    assert result['final_result'] == 'not_run' and result['handling_status'] == 'pending_human'
    assert 'sensitive exception' not in json.dumps(store.events(rid))


def test_service_precontrol_repair_has_trace_without_preflight(tmp_path):
    service = TaskService(tmp_path)
    rid = service.store.create({'mode': 'repair'})['run_id']
    service.store.claim(rid)
    service.store.request_control(rid, 'stop')
    service.run_claimed(rid)
    result = service.store.get(rid)
    assert result['status'] == 'stopped'
    assert result['result']['model_calls'] == result['result']['tool_calls'] == 0
    handoff = json.loads((service.store.directory(rid) / 'handoff.json').read_text(encoding='utf-8'))
    assert handoff['evidence_ids'] == [] and handoff['code_version'] is None
    assert not (service.store.directory(rid) / 'isolation').exists()


def test_handoff_and_trace_download_use_existing_artifact_and_bundle_routes(tmp_path):
    status, result, _, _, store, rid = execute(tmp_path, ['assertion_failed', 'environment_error'])
    with TestClient(create_app(tmp_path)) as client:
        path = result['handoff_path']
        response = client.get(f'/api/v3/tasks/{rid}/artifact', params={'path': path})
        assert response.status_code == 200 and response.json()['handling_status'] == 'pending_human'
        exported = client.get(f'/api/v3/tasks/{rid}/bundle')
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read('sha256.json'))
            assert 'handoff.json' in manifest and 'trace/configuration.json' in manifest
            assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest.items())
            assert json.loads(archive.read('report.json'))['run']['status'] == status


@pytest.mark.parametrize('unsettled', ['pending', 'unknown'])
def test_prior_day_uncertain_cost_blocks_other_provider_after_restart(tmp_path, unsettled):
    ledger = Ledger(tmp_path)
    ledger.reserve('old', 'old-run', settings(provider_id='provider_a'), 3, 100)
    with sqlite3.connect(ledger.path) as db:
        db.execute('UPDATE calls SET day=?,status=?', ('2000-01-01', unsettled))
    with pytest.raises(ModelBlocked, match='费用未知'):
        Ledger(tmp_path).reserve('new', 'new-run', settings(provider_id='provider_b'), 3, 100)
    summary = ledger.summary('old-run')
    assert summary['status'] == 'unknown' and summary['amount_usd'] is None


def test_legacy_ledger_migrates_without_changing_charges(tmp_path):
    path = tmp_path / 'model-ledger.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE calls (call_id TEXT PRIMARY KEY,run_id TEXT,day TEXT,reserved_microusd INTEGER,status TEXT,actual_microusd INTEGER)')
        db.execute("INSERT INTO calls VALUES ('a','r','2000-01-01',150,'unknown',NULL)")
    ledger = Ledger(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT call_id,run_id,day,reserved_microusd,status,actual_microusd FROM calls').fetchone() == ('a', 'r', '2000-01-01', 150, 'unknown', None)
    assert ledger.summary('r')['providers']['primary']['reserved_microusd'] == 150
    with pytest.raises(ModelBlocked):
        ledger.reserve('b', 'r2', settings(), 2, 100)


def test_shared_cap_cannot_be_reset_by_changing_provider(tmp_path):
    ledger = Ledger(tmp_path)
    a = settings(provider_id='a', daily_limit=Decimal('.00032'), max_output_tokens=10)
    ledger.reserve('a1', 'r', a, 10, 100)
    ledger.settle('a1', {'prompt_tokens': 100, 'completion_tokens': 10}, a)
    b = settings(provider_id='b', daily_limit=a.daily_limit, max_output_tokens=100)
    with pytest.raises(ModelBlocked, match='共享费用上限'):
        ledger.reserve('b1', 'r2', b, 10, 100)


def test_absolute_timeout_cancels_slow_body_and_blocks_subsequent_call(tmp_path):
    state = {'closed': False, 'requests': 0}
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.025)
                yield b' '
        async def aclose(self):
            state['closed'] = True
    def respond(request):
        state['requests'] += 1
        return httpx.Response(200, stream=SlowBody())
    model = LiveModel(settings(timeout=.1), Ledger(tmp_path), transport=httpx.MockTransport(respond))
    started = time.monotonic()
    record = model.call([{'role': 'user', 'content': 'offline'}], run_id='r', call_id='a', max_calls=3, deadline=started+3)
    assert record['failure_class'] == 'model_request_timeout'
    assert .075 <= time.monotonic()-started < 1.5 and state['closed']
    assert record['cost']['status'] == 'unknown' and not record['model_api_called']
    with pytest.raises(ModelBlocked):
        model.call([], run_id='r', call_id='b', max_calls=3, deadline=time.monotonic()+2)
    assert state['requests'] == 1


@pytest.mark.parametrize('response', ['redirect', 'too_large', 'missing_usage'])
def test_transport_failures_are_not_retried_or_zero_priced(tmp_path, response):
    calls = []
    def respond(request):
        calls.append(request)
        if response == 'redirect':
            return httpx.Response(302, headers={'location': 'https://other.invalid/'})
        if response == 'too_large':
            return httpx.Response(200, content=b'x' * 1_048_577)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    model = LiveModel(settings(), Ledger(tmp_path), transport=httpx.MockTransport(respond))
    record = model.call([], run_id='r', call_id='c', max_calls=2, deadline=time.monotonic()+3)
    assert len(calls) == 1 and record['status'] != 'ok'
    assert record['cost']['status'] == 'unknown' and record['cost']['amount_usd'] is None


@pytest.fixture
def profiles_root(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    original = Path(__file__).resolve().parents[1]
    for folder in ('configs', 'prompts', 'knowledge'):
        shutil.copytree(original / folder, root / folder)
    profile = {'provider_id': 'a', 'model': 'offline-model-a', 'base_url': 'https://a.invalid/v1',
               'api_key_env': 'CODE_FIX_A_KEY', 'approval_env': 'CODE_FIX_A_APPROVED',
               'input_price_env': 'CODE_FIX_A_INPUT', 'output_price_env': 'CODE_FIX_A_OUTPUT',
               'request_seconds_env': 'CODE_FIX_A_SECONDS', 'price_version': 'offline-test-v1',
               'max_input_tokens': 16000, 'max_output_tokens': 1000, 'token_limit_parameter': 'max_tokens'}
    second = dict(profile, provider_id='b', model='offline-model-b', base_url='https://b.invalid/v1',
                  api_key_env='CODE_FIX_B_KEY', token_limit_parameter='max_completion_tokens')
    (root / 'configs/model_profiles.json').write_text(json.dumps({'schema_version': '1.0.0', 'profiles': {'a': profile, 'b': second}}), encoding='utf-8')
    path = root / 'configs/agents.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['runtime_v3'].update(config_version='1.1.0', models={x: {'binding': 'approved_profile', 'profile_ref': x} for x in ('a', 'b')})
    for agent in data['runtime_v3']['agents']:
        agent['model_ref'] = 'b' if agent['agent_id'] == 'scheduler' else 'a'
    path.write_text(json.dumps(data), encoding='utf-8')
    for key, value in {'CODE_FIX_LIVE_APPROVED': '1', 'CODE_FIX_A_APPROVED': '1',
                       'CODE_FIX_A_KEY': 'offline-key-a', 'CODE_FIX_B_KEY': 'offline-key-b',
                       'CODE_FIX_A_INPUT': '1', 'CODE_FIX_A_OUTPUT': '2',
                       'CODE_FIX_A_SECONDS': '2', 'CODE_FIX_DAILY_USD': '.1'}.items():
        monkeypatch.setenv(key, value)
    return root


def test_independent_profiles_route_roles_redact_all_keys_and_share_ledger(profiles_root, tmp_path):
    config = RoleConfig(profiles_root)
    requests = []
    def respond(request):
        requests.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'offline-key-a offline-key-b'}}],
                                        'usage': {'prompt_tokens': 1, 'completion_tokens': 2}})
    router = ModelRouter(config, Ledger(tmp_path), transports={x: httpx.MockTransport(respond) for x in ('a', 'b')})
    for n, role in enumerate(('repairer', 'scheduler')):
        record = router.call_for_role(role, [], run_id='r', call_id=str(n), max_calls=3, deadline=time.monotonic()+3)
        assert record['raw_text'] == '[REDACTED] [REDACTED]' and not record['model_api_called']
    assert requests[0][0].startswith('https://a.invalid') and 'max_tokens' in requests[0][1]
    assert requests[1][0].startswith('https://b.invalid') and 'max_completion_tokens' in requests[1][1]
    assert set(router.ledger.summary('r')['providers']) == {'a', 'b'}
    assert router.binding('scheduler')['model_ref'] == 'b'


def test_unapproved_profile_cannot_be_used_or_fallback(profiles_root, monkeypatch):
    monkeypatch.delenv('CODE_FIX_A_APPROVED')
    with pytest.raises(ModelBlocked):
        ModelRouter.resolve(RoleConfig(profiles_root))


def test_partial_offline_router_cannot_accidentally_use_real_network(profiles_root, tmp_path):
    with pytest.raises(ConfigError, match='覆盖全部'):
        ModelRouter(RoleConfig(profiles_root), Ledger(tmp_path), transports={'a': httpx.MockTransport(lambda r: httpx.Response(200))})


@pytest.mark.parametrize('field,value', [('api_key', 'never-store-secrets'), ('base_url', 'http://a.invalid'),
    ('token_limit_parameter', 'unimplemented'), ('api_key_env', 'PATH')])
def test_profile_rejects_uncontrolled_or_unimplemented_fields(profiles_root, field, value):
    path = profiles_root / 'configs/model_profiles.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['profiles']['a'][field] = value
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ConfigError):
        load_profiles(profiles_root)


def test_event_cursor_is_incremental_and_default_compatible(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        token = client.get('/api/v3/session').json()['token']
        rid = client.post('/api/v3/tasks', headers={'X-Codefix-Token': token}, json={'mode': 'repair'}).json()['run_id']
        store = RunStore(tmp_path)
        store.event(rid, {'event': 'offline_a'})
        cursor = store.events(rid)[-1]['sequence']
        store.event(rid, {'event': 'offline_b'})
        url = f'/api/v3/tasks/{rid}/events'
        assert client.get(url).json() == store.events(rid)
        assert [x['event'] for x in client.get(url, params={'after_sequence': cursor}).json()] == ['offline_b']
        assert client.get(url, params={'after_sequence': -1}).status_code == 422
