"""Offline contract regression; never execute candidates or public programs."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from app.agents_v3 import AgentGateway, parse_role
from app.config import ConfigError, validate_draft_configs
from app.role_config import RoleConfig, PROTOCOL, digest
from app.run_store import RunStore
from app.workflow_v3 import Workflow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path):
    root = tmp_path / 'repo'
    for folder in ('configs', 'prompts', 'knowledge'):
        shutil.copytree(ROOT / folder, root / folder)
    return root


def edit(root, change):
    path = root / 'configs/agents.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    change(doc['runtime_v3'])
    path.write_text(json.dumps(doc), encoding='utf-8')


def test_default_config_and_legacy_version_remain_valid(root):
    assert validate_draft_configs(root) == []
    assert RoleConfig(root).snapshot['protocol'] == PROTOCOL
    assert json.loads((root / 'configs/agents.json').read_text())['config_version'] == 'iteration-0.2-20261101-draft'


@pytest.mark.parametrize('change', [
    lambda d: d.update(protocol='day1'),
    lambda d: d.update(config_version='3.0'),
    lambda d: d['agents'].append(copy.deepcopy(d['agents'][0])),
    lambda d: d['agents'].pop(),
    lambda d: d['agents'][1].update(model_ref='missing'),
    lambda d: d['agents'][1].update(model_ref=[]),
    lambda d: d['agents'][1].update(prompt_ref='task_assets/leap_legacy/prompts/repairer.md'),
    lambda d: d['agents'][1].update(output_schema='BarePython'),
    lambda d: d['agents'][1].update(tool_allowlist=['apply_patch']),
    lambda d: d['agents'][1].update(tool_allowlist=['run_tests']),
    lambda d: d['agents'][1].update(knowledge_ids=['../answer']),
    lambda d: d['agents'][1].update(enabled='false'),
    lambda d: d['models'].update(primary={'binding': 'arbitrary_import'}),
    lambda d: d.update(api_key='must-not-be-saved'),
])
def test_bad_config_rejected_by_selfcheck_and_runtime_before_any_calls(root, tmp_path, monkeypatch, change):
    edit(root, change)
    assert validate_draft_configs(root)
    monkeypatch.setattr('app.workflow_v3.ROOT', root)
    class Forbidden:
        def preflight(self, *args):
            pytest.fail('invalid config must precede executor preflight')
        def call(self, *args, **kwargs):
            pytest.fail('invalid config must not call model')
    store = RunStore(tmp_path / 'runs')
    rid = store.create({'mode': 'repair'})['run_id']
    status, result = Workflow(store, Forbidden(), Forbidden()).run(rid)
    assert status == 'blocked' and result['model_calls'] == result['tool_calls'] == 0
    assert not (store.directory(rid) / 'base').exists()


@pytest.mark.parametrize('role_index', range(4))
def test_disabled_role_blocks_whole_dynamic_workflow(root, role_index):
    edit(root, lambda d: d['agents'][role_index].update(enabled=False))
    config = RoleConfig(root)
    with pytest.raises(ConfigError):
        config.preflight()
    assert validate_draft_configs(root)


def invoke(root, tmp_path, monkeypatch):
    seen = {}
    class Model:
        mode = 'explicit_offline_stub'
        def call(self, messages, **kwargs):
            seen['material'] = json.loads(messages[1]['content'])
            return {'status': 'ok', 'model_api_called': False, 'raw_text': json.dumps({
                'changes': [{'path': 'code/leap_year.py', 'new_content': '# synthetic, never executed\n'}],
                'explanation': 'offline', 'evidence_ids': ['e1'], 'knowledge_citations': []})}
    store = RunStore(tmp_path / 'runs')
    rid = store.create({'mode': 'repair'})['run_id']
    config = RoleConfig(root)
    work = tmp_path / 'work'
    work.mkdir()
    gateway = AgentGateway(Model(), store, config)
    record = gateway.invoke('repairer', run_id=rid, round_number=1, code='x=1',
                            evidence={'phase': 'development', 'code_version': 'v1', 'evidence_id': 'e1'},
                            version='v1', work=work, task=store.get(rid)['task'], deadline=100)
    return config, record, seen, store.directory(rid)


@pytest.mark.parametrize('remove_tool,expected', [(False, ['business_rules']), (True, [])])
def test_knowledge_is_intersection_and_search_permission_enforced(root, tmp_path, monkeypatch, remove_tool, expected):
    def change(d):
        d['agents'][1]['knowledge_ids'] = ['business_rules']
        if remove_tool:
            d['agents'][1]['tool_allowlist'] = ['read_file']
    edit(root, change)
    calls = []
    def search(*args):
        calls.extend(args[1])
        return []
    monkeypatch.setattr('app.agents_v3.search_knowledge', search)
    config, record, seen, folder = invoke(root, tmp_path, monkeypatch)
    assert calls == expected and record['status'] == 'ok'
    assert seen['material']['knowledge_context'] == []
    provenance = record['provenance']
    assert provenance['config_sha256'] == digest(provenance['effective_config'])
    assert provenance['protocol'] == PROTOCOL and provenance['parser_version'] == '1.0.0'
    assert provenance['prompt_sha256'] == hashlib.sha256((root / 'prompts/repairer.md').read_bytes()).hexdigest()
    input_record = json.loads(next((folder / 'calls').glob('*/input.json')).read_text(encoding='utf-8'))
    assert input_record['provenance'] == provenance


def test_missing_read_permission_cannot_be_satisfied_by_code_material(root):
    edit(root, lambda d: d['agents'][1].update(tool_allowlist=['search_knowledge']))
    with pytest.raises(ConfigError):
        RoleConfig(root).require('repairer')
    assert validate_draft_configs(root)


def test_legacy_outputs_are_not_silently_accepted():
    for role, raw in [('repairer', 'def is_leap(y): return True'),
                      ('locator', '{"file":"code/leap_year.py","function":"is_leap","reason":"old"}')]:
        with pytest.raises(ValueError):
            parse_role(role, raw, ['e1'], [])


def test_hashes_capture_changes_without_rewriting_old_snapshots(root):
    before = RoleConfig(root)
    snapshot = copy.deepcopy(before.provenance('repairer'))
    edit(root, lambda d: d['agents'][1].update(knowledge_ids=[]))
    with (root / 'prompts/repairer.md').open('a', encoding='utf-8') as stream:
        stream.write('\nNew trusted prompt revision\n')
    after = RoleConfig(root)
    assert after.sha256 != before.sha256
    assert after.prompts['repairer'][1] != before.prompts['repairer'][1]
    assert snapshot == before.provenance('repairer')
