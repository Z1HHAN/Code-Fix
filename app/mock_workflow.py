"""An offline integration check; reviewed code runs, model candidates never do."""
from __future__ import annotations

import json
from pathlib import Path

from .baseline import baseline_record, write_json
from .config import read_json, load_task_spec
from .knowledge import search_knowledge
from .model_client import CallBudget, MockTransport, ModelClient
from .model_context import build_model_context
from .runner import append_event, code_version


def model_dry_run(root: Path, *, output_root: Path | None = None, mock_responses=None) -> dict:
    output = baseline_record(root, output_root=output_root, finalize=False)
    run = root / output['evidence_path']
    work = run / 'work'
    events = run / 'events.jsonl'
    version = output['summary']['code_version']
    original = None
    budget = CallBudget(max_calls=2, max_attempts_per_call=1)
    summary = {**output['summary'], 'mode': 'mock', 'mock_integration_passed': False,
               'candidate_applied': False, 'candidate_executed': False,
               'isolation_ready': False, 'calls_used': 0, 'source_unchanged': True,
               'failure_class': None, 'budget_scope': 'single_run_mock_model_attempts',
               'max_mock_attempts': 2, 'max_attempts_per_call': 1,
               'reason': '模拟联调：开发基线仍失败；仅保存候选，不应用、不执行、不做独立验收。'}
    try:
        result = output['tool_result']
        if result['counts'] != dict(collected=4, passed=3, failed=1, errors=0, skipped=0) or result['failure_class'] != 'assertion_failed':
            summary['failure_class'] = 'baseline_not_reproduced'
            return {**output, 'summary': summary}
        original = (work / 'code/leap_year.py').read_bytes()
        agents = read_json(root / 'configs/agents.json')
        task_spec = load_task_spec(root / 'configs/tasks/leap-baseline.json')
        task = {'task_id': task_spec.task_id,
                'description': '根据当前开发失败证据定位并建议修复闰年判断；本次为离线模拟，不执行候选。',
                'allowed_paths': list(task_spec.allowed_paths), 'forbidden_paths': ['tests']}
        # Shared binding ensures the locator's citations are available to the repairer.
        bindings = [set(a['knowledge_ids']) for a in agents['agents'] if a['agent_id'] in ('locator', 'repairer')]
        ids = tuple(sorted(set.intersection(*bindings)))
        knowledge = [{k: getattr(c, k) for k in ('citation_id', 'knowledge_id', 'source_path', 'excerpt')}
                     for c in search_knowledge(root, ids, '闰年 测试')]
        if not knowledge:
            raise ValueError('No reviewed shared knowledge')
        refs = [c['citation_id'] for c in knowledge]
        evidence = [{'evidence_id': 'e001', 'phase': 'development', 'code_version': version,
                     'summary': json.dumps({'counts': result['counts'], 'failure_class': result['failure_class']})}]
        locator = {'file': 'code/leap_year.py', 'function': 'is_leap',
                   'hypothesis': '模拟定位：世纪年边界未按业务规则处理。',
                   'evidence_ids': ['e001'], 'knowledge_citations': refs}
        repairer = {'changes': [{'path': 'code/leap_year.py', 'new_content':
                    '# Synthetic mock candidate; not model output or executed evidence.\n'
                    'def is_leap(year):\n    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\n'}],
                    'explanation': '模拟候选，仅验证JSON、引用与路径接口；不代表模型修复。',
                    'evidence_ids': ['e001'], 'knowledge_citations': refs}
        responses = [json.dumps(locator, ensure_ascii=False), json.dumps(repairer, ensure_ascii=False)] if mock_responses is None else mock_responses
        client = ModelClient(transport=MockTransport(responses), budget=budget)
        location = None
        for role in ('locator', 'repairer'):
            context = build_model_context(project_root=root, agents_config=agents, role=role,
                                          task=task, code={'code/leap_year.py': original.decode('utf-8')},
                                          evidence=evidence, knowledge=knowledge, work_root=work,
                                          code_version=version, location=location)
            write_json(run / f'{role}_context.json', {'mode': 'mock', 'role': role,
                       'code_version': version, 'messages': context.messages})
            record = client.invoke(context, run_id=output['run_id'], call_id=f'mock-{role}')
            write_json(run / f'{role}_call.json', record)
            append_event(events, {'event': 'model_call', **record})
            if record['status'] != 'ok':
                summary['failure_class'] = record['failure_class']
                return {**output, 'summary': summary}
            if role == 'locator':
                location = record['parsed_output']
            else:
                write_json(run / 'candidate.json', record['parsed_output'])
                append_event(events, {'event': 'candidate_saved', 'run_id': output['run_id'],
                                     'code_version': version, 'mode': 'mock', 'applied': False})
        summary['mock_integration_passed'] = True
    except Exception as exc:
        # No raw exception/provider text in the public result.
        summary.update(failure_class='mock_integration_error', error_type=type(exc).__name__)
    finally:
        summary['calls_used'] = budget.calls_used
        try:
            summary['source_unchanged'] = (original is not None and
                (work / 'code/leap_year.py').read_bytes() == original
                and code_version(work, ('code/leap_year.py',)) == version)
        except Exception as exc:
            summary.update(source_unchanged=False, source_check_error=type(exc).__name__)
        if not summary['source_unchanged']:
            summary['mock_integration_passed'] = False
            if summary['failure_class'] is None:
                summary['failure_class'] = 'unexpected_source_change'
        write_json(run / 'run_summary.json', summary)
        append_event(events, {'event': 'final', **summary})
        output['summary'] = summary
    return output
