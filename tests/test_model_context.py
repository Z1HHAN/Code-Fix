import json
from pathlib import Path

import pytest

from app.model_context import ContextError, build_model_context
from app.repair_output import InvalidModelOutput, parse_locator_output, parse_repair_output

ROOT = Path(__file__).resolve().parents[1]


def kwargs(tmp_path):
    return dict(project_root=ROOT,
                agents_config=json.loads((ROOT / 'configs/agents.json').read_text(encoding='utf-8')),
                role='locator', task={'task_id': 't', 'description': 'fixture', 'allowed_paths': ['code/a.py']},
                code={'code/a.py': 'x = 0\n'}, work_root=tmp_path, code_version='v1',
                evidence=[{'evidence_id': 'e1', 'phase': 'development', 'code_version': 'v1', 'summary': 'failed'}],
                knowledge=[{'citation_id': 'k1', 'knowledge_id': 'testing', 'source_path': 'knowledge/demo_project/testing.md', 'excerpt': 'fixture'}])


def test_context_only_contains_explicit_materials_and_no_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'DO-NOT-READ-THIS-KEY')
    ctx = build_model_context(**kwargs(tmp_path))
    text = json.dumps(ctx.messages, ensure_ascii=False)
    assert 'DO-NOT-READ-THIS-KEY' not in text
    assert ctx.evidence_ids == frozenset({'e1'})
    assert ctx.knowledge_ids == frozenset({'k1'})
    assert ctx.messages[0]['role'] == 'system' and ctx.messages[1]['role'] == 'user'


@pytest.mark.parametrize('role', ['tester', 'reviewer', 'unknown'])
def test_non_llm_or_unapproved_role_rejected(tmp_path, role):
    data = kwargs(tmp_path); data['role'] = role
    with pytest.raises(ContextError): build_model_context(**data)


@pytest.mark.parametrize('mutation', ['disabled', 'prompt', 'final_check', 'stale', 'extra_evidence_field', 'outside_code', 'knowledge'])
def test_rejects_unapproved_materials(tmp_path, mutation):
    data = kwargs(tmp_path)
    if mutation == 'disabled': data['agents_config']['agents'][0]['enabled'] = False
    if mutation == 'prompt': data['agents_config']['agents'][0]['prompt_ref'] = '../reference.md'
    if mutation == 'final_check': data['evidence'][0]['phase'] = 'final_check'
    if mutation == 'stale': data['evidence'][0]['code_version'] = 'v0'
    if mutation == 'extra_evidence_field': data['evidence'][0]['final_check_output'] = 'secret'
    if mutation == 'outside_code': data['code']['../reference.py'] = 'secret'
    if mutation == 'knowledge': data['knowledge'][0]['knowledge_id'] = 'reference_answers'
    with pytest.raises(ContextError): build_model_context(**data)


@pytest.mark.parametrize('value', [None, 1, 'e', [1], ['e', 'e']])
def test_reference_fields_require_unique_string_lists(value):
    payload = {'file': 'code/a.py', 'function': 'f', 'hypothesis': 'fixture',
               'evidence_ids': value, 'knowledge_citations': []}
    with pytest.raises(InvalidModelOutput): parse_locator_output(json.dumps(payload))


def test_parser_remains_compatible_but_checks_string_fields():
    payload = {'changes': [{'path': 'code/a.py', 'new_content': 'x = 1'}],
               'explanation': 'fixture', 'evidence_ids': [], 'knowledge_citations': []}
    assert parse_repair_output(json.dumps(payload)) == payload
    payload['changes'][0]['new_content'] = 1
    with pytest.raises(InvalidModelOutput): parse_repair_output(json.dumps(payload))
