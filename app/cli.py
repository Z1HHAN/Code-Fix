from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args()
    try:
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
    except (ConfigError, InvalidModelOutput, ValueError, OSError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
