"""Task service. Frozen baseline runs reuse the audited C executor unchanged."""
from pathlib import Path
import json
import os

from .baseline import baseline_record
from .run_store import RunStore
from .diagnostics import record_exception

ROOT = Path(__file__).resolve().parents[1]


class TaskService:
    def __init__(self, root=None):
        self.store = RunStore(root or ROOT / 'runs/platform')

    def execute(self, run_id):
        self.store.claim(run_id)
        self.run_claimed(run_id)
        return self.store.get(run_id)

    def run_claimed(self, run_id):
        from .redaction import credential_scope
        with credential_scope():
            return self._run_claimed(run_id)

    def _run_claimed(self, run_id):
        try:
            task = self.store.get(run_id)['task']
            control = self.store.get(run_id)['control']
            if control and task['mode'] == 'baseline':
                self.store.finish_if_running(run_id, 'paused' if control == 'pause' else 'stopped',
                                  {'final_result': 'not_run', 'reason': '按用户请求在执行前停止推进',
                                   'model_api_called': False, 'model_calls': 0, 'tool_calls': 0})
                return
            if task['mode'] == 'baseline' and task['project'] != 'leap':
                from .public_baseline import run_public_baseline
                status, result = run_public_baseline(self.store, run_id)
            elif task['mode'] == 'baseline':
                destination = self.store.directory(run_id) / 'baseline'
                # Preserve long evidence paths on Windows without changing C's
                # historical directory layout or shortening its identifiers.
                if os.name == 'nt' and not str(destination).startswith('\\\\?\\'):
                    destination = Path('\\\\?\\' + str(destination.resolve()))
                evidence = baseline_record(ROOT, output_root=destination)
                result = {**evidence['summary'], 'evidence_path': evidence['evidence_path'],
                          'tool_result': evidence['tool_result']}
                raw_root = Path(evidence['evidence_path'])
                if not raw_root.is_absolute():
                    raw_root = ROOT / raw_root
                log_path = raw_root / 'artifacts' / evidence['tool_result'].get('stdout_path', 'missing')
                if log_path.is_file():
                    with log_path.open('rb') as log:
                        result['stdout_excerpt'] = log.read(12000).decode('utf-8', errors='replace')
                status = 'blocked' if evidence['tool_result']['verification_status'] == 'not_run' else 'stopped'
                control = self.store.get(run_id)['control']
                if control:
                    status = 'paused' if control == 'pause' else 'stopped'
                    result['reason'] = '当前基线测试已结束；按用户请求停止后续推进'
            else:
                from .workflow_v3 import Workflow
                status, result = Workflow(self.store).run(run_id)
            self.store.finish_if_running(run_id, status, result)
        except Exception as exc:
            diagnostic = record_exception(self.store.directory(run_id), exc)
            self.store.finish_if_running(run_id, 'failed', {'final_result': 'not_run', **diagnostic,
                                               'reason': '运行异常，未完成独立验收'})

    def report(self, run_id):
        return {'schema_version': 'v3', **self.store.snapshot(run_id)}

    def write_report(self, run_id):
        target = self.store.directory(run_id) / 'report.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.report(run_id), ensure_ascii=False, indent=2), encoding='utf-8')
        return target
