"""Append-only execution facts and explicit pending-human handoffs for repair runs."""
import json
import time
import uuid


class RunTrace:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id
        self.directory = store.directory(run_id)
        self.parent = None
        self.active = None
        self.evidence_ids = []
        self.decisions = []

    def write(self, relative, value):
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never silently rewrite execution facts, including on error recovery.
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        return relative

    def start(self, node_id, *, round_number=0, phase='development', evidence_ids=()):
        if self.active is not None:
            raise ValueError('上一执行步骤尚未结束')
        self.active = {'trace_version': '1.0.0', 'run_id': self.run_id,
                       'step_id': 's-' + uuid.uuid4().hex, 'parent_step_id': self.parent,
                       'node_id': node_id, 'round': round_number, 'phase': phase,
                       'evidence_ids': list(evidence_ids)}
        self.started = time.monotonic()
        self.store.event(self.run_id, {'event': 'step_started', **self.active})
        return dict(self.active)

    def complete(self, status='ok', **facts):
        if self.active is None:
            return
        record = {**self.active, 'status': status, **facts,
                  'duration_ms': round((time.monotonic() - self.started) * 1000)}
        self.write('trace/steps/' + record['step_id'] + '.json', record)
        self.store.event(self.run_id, {'event': 'step_completed', **record})
        self.parent, self.active = record['step_id'], None

    def decision(self, *, program, effective_action, reason, evidence_ids, version,
                 suite_version, counters, advice=None, override_reason=None, round_number=0,
                 phase='development'):
        step = self.start('program_dispatch', round_number=round_number,
                          evidence_ids=evidence_ids, phase=phase)
        record = {**program, **step, 'decision_id': program.get('decision_id') or 'd-' + uuid.uuid4().hex,
                  'program_decision': program, 'model_advice': advice,
                  'action': effective_action, 'effective_action': effective_action,
                  'target_agent': {'repair': 'repairer', 'locate': 'locator'}.get(effective_action),
                  'reason': reason, 'override_reason': override_reason,
                  'rule_id': program.get('rule_id'), 'code_version': version,
                  'test_suite_version': suite_version, 'counters': counters}
        path = self.write('trace/decisions/' + record['decision_id'] + '.json', record)
        self.decisions.append(path)
        self.store.event(self.run_id, {'event': 'decision', 'artifact_path': path, **record})
        self.complete(effective_action=effective_action, decision_id=record['decision_id'])
        return record

    def handoff(self, *, status, result, task):
        handoff = {'schema_version': '1.0.0', 'run_id': self.run_id,
                   'handling_status': 'pending_human', 'automation_stopped': True,
                   'run_status': status, 'reason': result['reason'],
                   'project': task['project'], 'mode': result['mode'],
                   'model_api_called': result['model_api_called'],
                   'code_version': result.get('code_version'),
                   'code_path': result.get('code_path'),
                   'test_suite_version': result['test_suite_version'],
                   'dev_result': result['dev_result'], 'final_result': result['final_result'],
                   'evidence_ids': list(self.evidence_ids), 'decision_paths': list(self.decisions),
                   'consumption': {k: result[k] for k in ('rounds_used', 'model_calls', 'tool_calls', 'cost')},
                   'limits': {k: task[k] for k in ('max_rounds', 'max_calls', 'total_seconds')},
                   'next_steps': ['核对停止原因及关联证据', 'B/C对明确代码与测试版本复核',
                                  '满足授权与隔离条件后创建新任务；本运行不自动恢复'],
                   'accepted_by': None}
        path = self.write('handoff.json', handoff)
        self.store.event(self.run_id, {'event': 'handoff_created', 'handling_status': 'pending_human',
                                      'artifact_path': path, 'reason': result['reason']})
        return path
