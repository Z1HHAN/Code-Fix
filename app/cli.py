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
COMMAND_HELP = {
    'validate-config': '校验项目配置与登记任务',
    'decision-fixtures': '运行离线决策规则示例',
    'run-baseline': '复现冻结闰年基线，不调用模型',
    'model-dry-run': '检查模拟模型编排，不发真实请求',
    'start-task': '启动未归档的新建任务',
    'task-status': '读取任务状态与参数',
    'task-report': '生成任务报告，保留完整证据',
    'interrupt-task': '请求中断运行中的任务',
    'archive-task': '归档非运行任务，保留证据并从默认列表隐藏',
    'restore-task': '恢复已归档任务的列表可见性，不自动执行',
}


def run_baseline() -> int:
    output = baseline_record(ROOT)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    result = output['tool_result']
    return 0 if result['status'] in {'ok', 'failed'} and result['verification_status'] in {'passed', 'failed'} else 2


def main() -> int:
    parser = argparse.ArgumentParser(description='Code-Fix V3任务与证据；模型调用需明确授权及配置')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('validate-config', 'decision-fixtures', 'run-baseline', 'model-dry-run'):
        sub.add_parser(name, help=COMMAND_HELP[name])
    parse = sub.add_parser('parse-output', help='校验定位或修复模型输出文件')
    parse.add_argument('kind', choices=['locator', 'repairer'])
    parse.add_argument('path')
    create = sub.add_parser('create-task', help='保存任务参数，不自动启动')
    create.add_argument('--mode', choices=['baseline', 'repair'], default='baseline')
    create.add_argument('--strategy', choices=['fixed', 'dynamic', 'single'], help='dynamic多Agent、single单修复员实验、fixed固定对照；保存配置优先')
    create.add_argument('--config-id', help='引用已保存的不可变配置')
    create.add_argument('--description')
    create.add_argument('--project', default='leap')
    create.add_argument('--max-rounds', type=int, default=3)
    create.add_argument('--max-calls', type=int, default=12)
    create.add_argument('--total-seconds', type=int, default=300)
    create.add_argument('--idempotency-key')
    for name in ('start-task', 'task-status', 'task-report', 'interrupt-task', 'archive-task', 'restore-task'):
        command = sub.add_parser(name, help=COMMAND_HELP[name])
        command.add_argument('run_id')
    listing = sub.add_parser('list-tasks', help='列出最近任务，默认隐藏已归档任务')
    listing.add_argument('--include-archived', action='store_true', help='同时列出已归档任务')
    sub.add_parser('list-projects', help='列出已登记题目与执行条件')
    importer = sub.add_parser('import-quixbugs', help='预览QuixBugs资产导入；仅--write时写入')
    importer.add_argument('--source-bundle')
    importer.add_argument('--write', action='store_true', help='默认只预览；指定后导入已校验资产并更新目录')
    sub.add_parser('doctor', help='查看本机环境概览，不执行隔离探针')
    sub.add_parser('provider-check', help='仅查询已登记账号的模型列表，不调用付费聊天')
    sub.add_parser('budget-status', help='本项目人民币额度估算，不是平台实时余额')
    smoke = sub.add_parser('provider-smoke', help='一次有上限的JSON接口测试；不是修复闭环')
    smoke.add_argument('--profile', default='sf_account1_v32')
    serve = sub.add_parser('serve', help='启动本机Web工作台，正常关闭后退出')
    serve.add_argument('--port', type=int, default=8000, help='固定监听端口1024至65535；不支持0随机分配')
    reconcile = sub.add_parser('budget-reconcile', help='人工对账；仅本机CLI，不调用模型、不改变额度')
    reconcile.add_argument('--call-id', required=True)
    reconcile.add_argument('--currency', choices=['CNY','USD'], required=True)
    reconcile.add_argument('--amount', required=True)
    reconcile.add_argument('--evidence', required=True)
    reconcile.add_argument('--operator', required=True)
    reconcile.add_argument('--reason', required=True)
    reconcile.add_argument('--confirm-no-inflight', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'budget-reconcile':
            from .live_model import Ledger
            from .reconciliation import reconcile
            ledger = Ledger(Path(os.environ.get('CODE_FIX_RUN_ROOT') or ROOT / 'runs/platform'))
            result = reconcile(ledger, call_id=args.call_id, currency=args.currency, amount=args.amount, evidence=args.evidence, operator=args.operator, reason=args.reason, no_inflight=args.confirm_no_inflight)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command in {'provider-check', 'provider-smoke', 'budget-status'}:
            from .siliconflow import check_models, smoke, budget_status
            root = Path(os.environ.get('CODE_FIX_RUN_ROOT') or ROOT / 'runs/platform')
            result = check_models() if args.command == 'provider-check' else budget_status(root) if args.command == 'budget-status' else smoke(root, args.profile)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if args.command != 'provider-smoke' or result['json_contract_passed'] else 2
        if args.command == 'import-quixbugs':
            from .quixbugs_import import BUNDLE, import_dataset
            result = import_dataset(source_bundle=args.source_bundle or BUNDLE, write=args.write)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'list-projects':
            from .tasks_v3 import list_tasks
            print(json.dumps(list_tasks(), ensure_ascii=False, indent=2))
            return 0
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
        if args.command in {'create-task', 'start-task', 'task-status', 'task-report', 'list-tasks', 'interrupt-task', 'archive-task', 'restore-task'}:
            from .service import TaskService
            service = TaskService(os.environ.get('CODE_FIX_RUN_ROOT'))
            if args.command == 'create-task':
                from .tasks_v3 import get_task
                result = service.store.create({'mode': args.mode, 'project': args.project,
                                               'description': args.description if args.description is not None else get_task(args.project).data['description'],
                                               'max_rounds': args.max_rounds, 'max_calls': args.max_calls,
                                               'total_seconds': args.total_seconds,
                                               **({'strategy': args.strategy} if args.strategy else {}),
                                               **({'config_id': args.config_id} if args.config_id else {})}, args.idempotency_key)
            elif args.command == 'start-task':
                result = service.execute(args.run_id)
            elif args.command == 'task-status':
                result = service.store.get(args.run_id)
            elif args.command == 'task-report':
                result = {'report_path': str(service.write_report(args.run_id))}
            elif args.command == 'interrupt-task':
                service.store.interrupt(args.run_id)
                result = service.store.get(args.run_id)
            elif args.command in {'archive-task', 'restore-task'}:
                result = service.store.set_archived(args.run_id, args.command == 'archive-task')
            else:
                result = service.store.list(include_archived=args.include_archived)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if args.command not in {'archive-task', 'restore-task'} and isinstance(result, dict) and result.get('status') in {'blocked', 'failed'} else 0
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
        if args.command == 'decision-fixtures':
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
        parser.error(f'命令未实现：{args.command}')
    except (ConfigError, InvalidModelOutput, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
