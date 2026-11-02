"""Real reviewed baseline evidence, synthetic model replies, no candidate execution."""
import json
from pathlib import Path

from app.mock_workflow import model_dry_run

ROOT = Path(__file__).resolve().parents[1]


def test_mock_roles_use_current_evidence_without_applying_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'FORBIDDEN-KEY-MARKER')
    output = model_dry_run(ROOT, output_root=tmp_path)
    run = Path(output['evidence_path'])
    summary = output['summary']
    assert summary['mock_integration_passed'] is True
    assert summary['status'] == 'stopped'
    assert summary['dev_result'] == 'failed' and summary['final_result'] == 'not_run'
    assert summary['model_api_called'] is False
    assert summary['candidate_applied'] is False and summary['candidate_executed'] is False
    assert summary['source_unchanged'] is True and summary['calls_used'] == 2
    assert output['tool_result']['counts'] == dict(collected=4, passed=3, failed=1, errors=0, skipped=0)
    original = (ROOT / 'c_fixtures/leap_original/code/leap_year.py').read_bytes()
    assert (run / 'work/code/leap_year.py').read_bytes() == original
    candidate = json.loads((run / 'candidate.json').read_text(encoding='utf-8'))
    assert candidate['changes'][0]['path'] == 'code/leap_year.py'
    for role in ('locator', 'repairer'):
        context = json.loads((run / f'{role}_context.json').read_text(encoding='utf-8'))
        text = json.dumps(context, ensure_ascii=False)
        assert 'FORBIDDEN-KEY-MARKER' not in text
        material = json.loads(context['messages'][1]['content'])
        assert material['dev_evidence'][0]['code_version'] == summary['code_version']
        assert material['dev_evidence'][0]['phase'] == 'development'
        assert material['knowledge_context']
        assert set(material['current_code']) == {'code/leap_year.py'}
        record = json.loads((run / f'{role}_call.json').read_text(encoding='utf-8'))
        assert record['status'] == 'ok' and record['mode'] == 'mock'
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [e['event'] for e in events] == ['run_started', 'test_run', 'model_call', 'model_call', 'candidate_saved', 'final']


def test_invalid_mock_json_stops_without_candidate(tmp_path):
    output = model_dry_run(ROOT, output_root=tmp_path, mock_responses=['not-json'])
    assert output['summary']['mock_integration_passed'] is False
    assert output['summary']['failure_class'] == 'invalid_model_output'
    assert output['summary']['calls_used'] == 1
    assert not (Path(output['evidence_path']) / 'candidate.json').exists()
    events = (Path(output['evidence_path']) / 'events.jsonl').read_text(encoding='utf-8').splitlines()
    assert json.loads(events[-1])['event'] == 'final'


def test_version_recheck_error_preserves_failed_final(tmp_path, monkeypatch):
    from app import mock_workflow
    def unavailable(*args):
        raise OSError('fixture-read-error')
    monkeypatch.setattr(mock_workflow, 'code_version', unavailable)
    output = model_dry_run(ROOT, output_root=tmp_path)
    assert output['summary']['source_unchanged'] is False
    assert output['summary']['mock_integration_passed'] is False
    assert output['summary']['failure_class'] == 'unexpected_source_change'
    assert output['summary']['source_check_error'] == 'OSError'
    events = (Path(output['evidence_path']) / 'events.jsonl').read_text(encoding='utf-8').splitlines()
    assert json.loads(events[-1])['event'] == 'final'
