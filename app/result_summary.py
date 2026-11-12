"""One evidence-derived result for UI, reports and ZIP; no extra execution."""
import difflib
from datetime import datetime
from .tasks_v3 import get_task
from .security import normalise_relpath


def summarize(store, snapshot):
    run, events = snapshot['run'], snapshot['events']
    result = run.get('result') or {}
    definition = get_task(run['task']['project'])
    folder = store.directory(run['run_id'])
    def artifact(relative):
        if not relative:
            return None
        relative = normalise_relpath(relative)
        p = folder
        for part in relative.split('/'):
            p = p / part
            if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
                return None
        if not p.is_file() or p.stat().st_size > 1048576:
            return None
        return {'path': relative, 'content': p.read_text(encoding='utf-8', errors='replace')}
    tests = []
    for e in events:
        if e['event'] != 'test_completed':
            continue
        stage = 'final' if e.get('phase') == 'final_check' else 'baseline' if e.get('round') == 0 else 'candidate'
        failure = e.get('failure_class')
        reproduced = stage == 'baseline' and failure in {'assertion_failed', 'code_runtime_error'}
        meaning = ('缺陷已复现，可以开始修复' if reproduced else '起点未复现缺陷' if stage == 'baseline' and failure == 'passed'
                   else '最终复验通过' if stage == 'final' and failure == 'passed'
                   else '候选通过，待最终复验' if stage == 'candidate' and failure == 'passed'
                   else '候选仍未修好' if stage == 'candidate' and failure in {'assertion_failed','syntax_error','code_runtime_error'}
                   else '最终复验未通过' if stage == 'final' else '执行异常或证据不足')
        tests.append({'stage': stage, 'round': e.get('round'), 'verification_status': e.get('verification_status'),
                      'failure_class': failure, 'counts': e.get('counts'), 'meaning': meaning,
                      'evidence_path': e.get('evidence_path'), 'step_id': e.get('step_id'),
                      'code_version': e.get('code_version'), 'test_suite_version': e.get('test_suite_version')})
    patches = [e for e in events if e['event'] == 'candidate_applied']
    patch = patches[-1] if patches else None
    calls = [e for e in events if e['event'] == 'agent_call']
    requests = (sum(e['model_api_called'] is True for e in calls) if all(type(e.get('model_api_called')) is bool for e in calls) else None) if calls else result.get('actual_model_requests')
    before = artifact('base/' + definition.source)
    after = artifact(result.get('code_path')) if patch else None
    combined_diff = ''.join(difflib.unified_diff(before['content'].splitlines(True), after['content'].splitlines(True),
        fromfile='original/' + definition.source, tofile='candidate/' + definition.source)) if before and after else None
    started = next((e.get('created_at') for e in events if e['event'] == 'started'), None)
    finished = next((e.get('created_at') for e in reversed(events) if e['event'] == 'finished'), None)
    duration_ms = round((datetime.fromisoformat(finished)-datetime.fromisoformat(started)).total_seconds()*1000) if started and finished else None
    return {'schema_version': 1, 'run_id': run['run_id'], 'status': run['status'],
            'success': run['status'] == 'success' and result.get('final_result') == 'passed',
            'reason': result.get('reason'), 'rounds_used': result.get('rounds_used'),
            'max_rounds': run['task']['max_rounds'], 'model_attempts': len(calls) if calls else result.get('model_attempts'),
            'actual_model_requests': requests, 'tests': tests, 'cost': result.get('cost'),
            'model_output_issues': [{'role': e.get('role'), 'round': e.get('round'),
                'call_id': e.get('call_id'), 'reason': e.get('reason'),
                'finish_reason': e.get('finish_reason'), 'diagnostics': e['output_diagnostics']}
                for e in calls if e.get('output_diagnostics')],
            'started_at': started, 'finished_at': finished, 'duration_ms': duration_ms,
            'before': before, 'after': after, 'combined_diff': combined_diff,
            'diff': artifact(f"patch-{patch['round']}/patch.diff") if patch else None,
            'explanations': [{'round': e.get('round'), 'explanation': e.get('parsed_output', {}).get('explanation')}
                for e in calls if e.get('role') == 'repairer' and e.get('parsed_output')],
            'checks': [e for e in events if e['event'] == 'guard_check'],
            'skips': [e for e in events if e['event'] == 'roles_skipped']}
