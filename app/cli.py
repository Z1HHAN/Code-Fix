from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .baseline import baseline_record
from .config import ConfigError, load_task_spec, read_json, validate_draft_configs
from .decision import DecisionEngine
from .repair_output import InvalidModelOutput, parse_locator_output, parse_repair_output

ROOT = Path(__file__).resolve().parents[1]


def run_baseline() -> int:
    output = baseline_record(ROOT)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    result = output['tool_result']
    return 0 if result['status'] in {'ok', 'failed'} and result['verification_status'] in {'passed', 'failed'} else 2


def main() -> int:
    parser = argparse.ArgumentParser(description='Code-Fix组件与模拟联调；无真实API入口')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('validate-config', 'decision-fixtures', 'run-baseline', 'model-dry-run'):
        sub.add_parser(name)
    parse = sub.add_parser('parse-output')
    parse.add_argument('kind', choices=['locator', 'repairer'])
    parse.add_argument('path')
    create = sub.add_parser('create-task')
    create.add_argument('--mode', choices=['baseline', 'repair'], default='baseline')
    create.add_argument('--description', default='修复闰年判断')
    create.add_argument('--max-rounds', type=int, default=3)
    create.add_argument('--idempotency-key')
    for name in ('start-task', 'task-status', 'task-report', 'interrupt-task'):
        command = sub.add_parser(name)
        command.add_argument('run_id')
    sub.add_parser('list-tasks')
    sub.add_parser('doctor')
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            from .readiness import readiness
            print(json.dumps(readiness(), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'serve':
            import uvicorn
            from .api import create_app
            if not 1024 <= args.port <= 65535:
                raise ValueError('端口须为1024至65535')
            uvicorn.run(create_app(), host='127.0.0.1', port=args.port)
            return 0
        if args.command in {'create-task', 'start-task', 'task-status', 'task-report', 'list-tasks', 'interrupt-task'}:
            from .service import TaskService
            service = TaskService(os.environ.get('CODE_FIX_RUN_ROOT'))
            if args.command == 'create-task':
                result = service.store.create({'mode': args.mode, 'description': args.description,
                                               'max_rounds': args.max_rounds}, args.idempotency_key)
            elif args.command == 'start-task':
                result = service.execute(args.run_id)
            elif args.command == 'task-status':
                result = service.store.get(args.run_id)
            elif args.command == 'task-report':
                result = {'report_path': str(service.write_report(args.run_id))}
            elif args.command == 'interrupt-task':
                service.store.interrupt(args.run_id)
                result = service.store.get(args.run_id)
            else:
                result = service.store.list()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if isinstance(result, dict) and result.get('status') in {'blocked', 'failed'} else 0
        if args.command == 'validate-config':
            task = load_task_spec(ROOT / 'configs/tasks/leap-baseline.json')
            errors = validate_draft_configs(ROOT)
            print(json.dumps({'task_id': task.task_id, 'errors': errors}, ensure_ascii=False, indent=2))
            return 1 if errors else 0
        if args.command == 'run-baseline':
            return run_baseline()
        if args.command == 'model-dry-run':
            from .mock_workflow import model_dry_run
            output = model_dry_run(ROOT)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0 if output['summary']['mock_integration_passed'] else 2
        if args.command == 'parse-output':
            raw = Path(args.path).read_text(encoding='utf-8')
            value = parse_locator_output(raw) if args.kind == 'locator' else parse_repair_output(raw)
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        engine = DecisionEngine(read_json(ROOT / 'configs/decision_rules.json'))
        common = dict(phase='development', isolation_ready=True, user_pause_requested=False,
                      evidence_matches_current_version=True, repair_budget_available=True,
                      execution_budget_available=True, needs_new_repair=True,
                      next_action_requires_execution_budget=True, output_agent=None, retries_left=2)
        results = [engine.decide({**common, 'failure_class': failure}, run_id='fixture-run',
                                 code_version='fixture:v0', evidence_ids=['fixture-e1'],
                                 decision_id=f'D{i:03d}')
                   for i, failure in enumerate(('assertion_failed', 'syntax_error', 'environment_error', 'timeout'), 1)]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    except (ConfigError, InvalidModelOutput, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
