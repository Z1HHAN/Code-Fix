"""Run the original frozen task through C's executor, without a model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

from .config import ConfigError, load_task_spec
from .runner import CODE_VERSION_SCHEME, append_event, code_version, prepare_fixture, run_tests

SHANGHAI = timezone(timedelta(hours=8))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def baseline_record(root: Path, *, output_root: Path | None = None, finalize: bool = True) -> dict:
    task = load_task_spec(root / 'configs/tasks/leap-baseline.json')
    if task.test_profile != 'leap_original':
        raise ConfigError('基线入口仅允许已冻结的leap_original，不接受任意命令')
    run_id = datetime.now(SHANGHAI).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
    run_dir = (output_root if output_root is not None else root / 'runs') / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    common = {'run_id': run_id, 'task_id': task.task_id, 'run_type': 'tool_check'}
    events = run_dir / 'events.jsonl'
    version = None
    append_event(events, {**common, 'event': 'run_started', 'phase': 'development',
                          'code_version': version, 'code_version_scheme': CODE_VERSION_SCHEME})
    try:
        work = prepare_fixture(task.test_profile, run_dir / 'work')
        version = code_version(work, task.allowed_paths)
        result = run_tests(**{k: common[k] for k in ('run_id', 'task_id')},
                           workspace=work, test_profile=task.test_profile,
                           timeout_seconds=10, code_version=version, artifact_dir=run_dir / 'artifacts')
    except Exception as exc:
        result = {**common, 'tool': 'run_tests', 'call_id': None, 'phase': 'development',
                  'status': 'blocked', 'verification_status': 'not_run',
                  'failure_class': 'environment_error', 'code_version': version,
                  'counts': dict.fromkeys(('collected', 'passed', 'failed', 'errors', 'skipped')),
                  'error_type': type(exc).__name__, 'reason': '基线准备或控制程序异常；无可接受测试证据。'}
    write_json(run_dir / 'tool_result.json', result)
    append_event(events, {**common, 'event': 'test_run', 'phase': 'development',
                          'call_id': result['call_id'], 'code_version': version,
                          'evidence_id': 'e001', 'failure_class': result['failure_class'],
                          'status': result['status']})
    summary = {**common, 'status': 'stopped', 'dev_result': result['verification_status'],
               'final_result': 'not_run', 'code_version': version,
               'code_version_scheme': CODE_VERSION_SCHEME, 'model_api_called': False,
               'failure_class': result['failure_class'],
               'reason': '仅检查原始任务基线；未调用模型，未执行独立验收。'}
    write_json(run_dir / 'run_summary.json', summary)
    if finalize:
        append_event(events, {'event': 'final', **summary})
    return {**common, 'tool_result': result, 'summary': summary,
            'evidence_path': str(run_dir.relative_to(root)) if run_dir.is_relative_to(root) else str(run_dir)}
