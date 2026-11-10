"""Cloud operator commands; does not configure or call any model provider."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import uuid

from .remote_execution import create_execution_app, write_json


def main():
    parser = argparse.ArgumentParser(description='Code Fix remote isolation service')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('create-access', 'revoke-access'):
        p = sub.add_parser(name)
        p.add_argument('--id', required=True)
        p.add_argument('--tokens-file', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8780)
    serve.add_argument('--concurrency', type=int, default=1)
    qualify = sub.add_parser('qualify', help='Run registered bad baseline in Docker, without model requests')
    qualify.add_argument('--project', default='qb-get_factors')
    args = parser.parse_args()
    if args.command == 'qualify':
        from .split_runner import SplitDockerRunner
        from .tasks_v3 import get_task
        from .runner import code_version
        task = get_task(args.project)
        if task.admission_blocker:
            parser.error(task.admission_blocker)
        folder = Path(os.environ.get('CODE_FIX_EXECUTION_ROOT', 'runs/remote-execution')).parent / ('qualification-' + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        runner = SplitDockerRunner(task.project)
        probe = runner.preflight(folder / 'isolation')
        write_json(folder / 'qualification.json', {'project': task.project, 'probe': probe})
        if not probe['isolation_ready']:
            print(json.dumps({'passed': False, 'probe': probe, 'evidence': str(folder)}, ensure_ascii=False))
            raise SystemExit(2)
        work = task.prepare(folder / 'bad-baseline')
        result = runner.run(work, folder / 'baseline', phase='development', timeout=60,
                            expected_version=code_version(work, task.profile['source_paths']))
        accepted = result.get('verification_status') == 'failed' and result.get('failure_class') in {'assertion_failed', 'code_runtime_error'}
        summary = {'passed': accepted, 'scope': 'registered bad-baseline qualification; no model request or repair',
                   'project': task.project, 'image': runner.image,
                   'test_suite_version': task.summary()['test_suite_version'],
                   'verification_status': result.get('verification_status'), 'failure_class': result.get('failure_class'),
                   'cleanup_passed': result.get('cleanup_passed'), 'evidence': str(folder)}
        write_json(folder / 'qualification.json', summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not accepted:
            raise SystemExit(2)
        return
    if args.command == 'serve':
        import uvicorn
        uvicorn.run(create_execution_app(concurrency=args.concurrency), host=args.host, port=args.port,
                    workers=1, proxy_headers=False)
        return
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', args.id):
        parser.error('访问者ID只接受字母、数字、下划线或短横线，最多64字符')
    path = Path(args.tokens_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if args.command == 'create-access':
        if args.id in registry:
            parser.error('访问者已存在；若需换码，请先撤销原访问码')
        token = secrets.token_urlsafe(32)
        registry[args.id] = hashlib.sha256(token.encode()).hexdigest()
    else:
        if args.id not in registry:
            parser.error('访问者不存在')
        del registry[args.id]
    write_json(path, registry)
    if os.name != 'nt':
        path.chmod(0o600)
    if args.command == 'create-access':
        print('访问码仅显示这一次，请直接交给该使用者，不要写入安装包或日志：')
        print(token)
    else:
        print('已撤销访问码，新请求立即拒绝；已在执行的有限时任务仍按原时限结束。')


if __name__ == '__main__':
    main()
