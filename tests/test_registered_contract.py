"""Contract clarification reaches requests and identity, without test assets."""
import json
from pathlib import Path

from app.tasks_v3 import get_task
from tests.test_workflow_v3 import execute


def test_wrap_contract_reaches_summary_model_request_and_frozen_identity(tmp_path):
    definition = get_task('qb-wrap')
    original = definition.profile['approved_files'].copy()
    status, result, runner, model, store, rid = execute(tmp_path,
        ['assertion_failed', 'passed', 'passed'], {'project': 'qb-wrap', 'description': 'user description'})
    assert status == 'success' and model.roles == ['repairer', 'detector']  # explicit offline doubles
    folder = store.directory(rid)
    request = json.loads(next(folder.glob('calls/*/input.json')).read_text(encoding='utf-8'))
    material = json.loads(request['messages'][1]['content'])
    contract = definition.summary()['registered_contract']
    assert material['task']['registered_contract'] == contract
    assert material['task']['description'] == 'user description'
    identity = json.loads((folder/'trace/experiment.json').read_text(encoding='utf-8'))['identity']
    assert identity['registered_contract'] == contract
    assert 'test_input' not in json.dumps(material) and 'correct_python_programs/' not in json.dumps(material)
    assert definition.profile['approved_files'] == original
    definition.verify(folder/'base')


def test_unaffected_task_has_no_wrap_contract():
    assert 'registered_contract' not in get_task('leap').summary()


def test_wrap_registered_preservation_contract_is_consistent_with_frozen_data():
    task = get_task('qb-wrap')
    contract = task.data['registered_contract']
    assert contract['preserve_text'] is True and contract['separator_position'] == 'next_line'
    # Developer-only audit: no examples/expected outputs enter model material.
    rows = [json.loads(line) for line in
            (Path(task.profile['fixture_dir'])/'json_testcases/wrap.json').read_text().splitlines()]
    assert rows and all(''.join(expected) == inputs[0] for inputs, expected in rows)
