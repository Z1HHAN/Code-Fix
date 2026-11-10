"""Authenticated test jobs. Model requests and credentials never enter this service."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Request

from .docker_runner import IsolationBlocked
from .runner import code_version
from .split_runner import SplitDockerRunner
from .tasks_v3 import get_task

PROTOCOL = 'codefix-execution-v1'
TERMINAL = {'completed', 'failed', 'cancelled', 'expired', 'interrupted'}
MAX_BODY = 524288
MAX_EVIDENCE = 33554432
# Do not export workspaces, reviewed test sources, or any host-selected paths.
EVIDENCE_NAMES = (
    'isolation/isolation.json', 'isolation/container.json', 'isolation/cleanup.json',
    'isolation/stdout.txt', 'isolation/stderr.txt', 'test/result.json',
    'test/split-cleanup.json', 'test/candidate-stdout.txt', 'test/candidate-stderr.txt',
    'test/trusted-run/stdout.txt', 'test/trusted-run/stderr.txt',
    'test/trusted-run/container.json', 'test/trusted-run/cleanup.json',
    'test/trusted-run/output/junit.xml', 'test/trusted-run/output/pytest_events.json',
)


def service_identity():
    root = Path(__file__).resolve().parents[1]
    files = {}
    for directory in ('app', 'docker', 'c_runner'):
        for path in (root / directory).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.json'}:
                files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    path = root / 'configs/tasks/catalog-v3.json'
    files['configs/tasks/catalog-v3.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def validate_job(value):
    fields = {'protocol', 'kind', 'project', 'request_id', 'expires_in', 'phase',
              'source', 'code_version', 'test_suite_version', 'test_seconds', 'image'}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError('未知执行字段；不接受密钥、命令、URL或文件路径')
    if value.get('protocol') != PROTOCOL or value.get('kind') not in {'probe', 'test'}:
        raise ValueError('执行协议或任务类型不支持')
    task = get_task(value.get('project'))
    if task.admission_blocker:
        raise ValueError(task.admission_blocker)
    rid = value.get('request_id')
    if not isinstance(rid, str) or not 1 <= len(rid) <= 100 or not rid.replace('-', '').isalnum():
        raise ValueError('请求编号无效')
    for field, maximum in (('expires_in', 300), ('test_seconds', 60)):
        number = value.get(field)
        if type(number) not in (int, float) or not math.isfinite(number) or not .01 <= number <= maximum:
            raise ValueError('任务时限须在允许范围内')
    if value.get('test_suite_version') != task.summary()['test_suite_version']:
        raise ValueError('客户端与执行服务器的测试版本不一致')
    if value['kind'] == 'test':
        if value.get('phase') not in {'development', 'final_check'}:
            raise ValueError('测试阶段无效')
        if not isinstance(value.get('source'), str) or len(value['source'].encode('utf-8')) > 262144:
            raise ValueError('候选源码过大或不是文本')
        if not isinstance(value.get('code_version'), str) or not isinstance(value.get('image'), str):
            raise ValueError('候选版本及探针镜像身份必填')
    elif set(value) & {'source', 'code_version', 'phase', 'image'}:
        raise ValueError('隔离探针不接受候选源码')
    return task


class ExecutionJobs:
    def __init__(self, root, tokens, *, concurrency=1, runner_factory=SplitDockerRunner):
        if type(concurrency) is not int or not 1 <= concurrency <= 4:
            raise ValueError('执行并发须为1至4；默认1')
        if (not isinstance(tokens, dict) or not tokens or
                any(not isinstance(k, str) or not k or not isinstance(v, str)
                    or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
                    for k, v in tokens.items()) or len(set(tokens.values())) != len(tokens)):
            raise ValueError('访问码登记须为独立用户ID到SHA256的映射')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tokens, self.concurrency, self.runner_factory = tokens, concurrency, runner_factory
        self.build_sha256 = service_identity()
        self.lock = threading.RLock()
        self.jobs, self.requests, self.cancelled = {}, {}, {}
        self.pool = None
        self.process_lock = None

    def start(self):
        # One scheduler owns a job root. Multiple ASGI workers must not replay jobs.
        self.process_lock = (self.root / 'scheduler.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.process_lock.seek(0)
                self.process_lock.write(b'0'); self.process_lock.flush(); self.process_lock.seek(0)
                msvcrt.locking(self.process_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            raise RuntimeError('执行目录已有调度进程；服务必须使用一个ASGI worker') from None
        for path in self.root.glob('job-*/job.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['status'] not in TERMINAL:
                record.update(status='interrupted', reason='服务器重启，任务未自动重跑')
                write_json(path, record)
            self.jobs[record['job_id']] = record
            self.requests[(record['owner'], record['request_id'])] = record['job_id']
        self.pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix='isolation')

    def close(self):
        with self.lock:
            for event in self.cancelled.values():
                event.set()
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.process_lock.close()

    def persist(self, record):
        write_json(self.root / record['job_id'] / 'job.json', record)

    def submit(self, owner, value):
        validate_job(value)
        identity = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            previous = self.requests.get((owner, value['request_id']))
            if previous:
                if self.jobs[previous]['request_sha256'] != identity:
                    raise HTTPException(409, '同一请求编号对应不同内容')
                return self.view(owner, previous)
            active = [j for j in self.jobs.values() if j['status'] not in TERMINAL]
            if len(active) >= 32 or sum(j['owner'] == owner for j in active) >= 8:
                raise HTTPException(429, '执行队列已满，请稍后提交')
            used = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            if used >= 1_073_741_824:
                raise HTTPException(507, '执行证据存储已满，请联系管理员归档')
            jid = 'job-' + uuid.uuid4().hex
            folder = self.root / jid
            folder.mkdir()
            record = dict(job_id=jid, owner=owner, request_id=value['request_id'], request_sha256=identity,
                          status='queued', created_at=time.time(), expires_at=time.time() + value['expires_in'],
                          project=value['project'], kind=value['kind'], result=None, reason=None,
                          server_build_sha256=self.build_sha256)
            write_json(folder / 'request.json', value)
            self.jobs[jid] = record
            self.requests[(owner, value['request_id'])] = jid
            self.cancelled[jid] = threading.Event()
            self.persist(record)
            self.pool.submit(self.execute, jid, value)
            return self.view(owner, jid)

    def view(self, owner, jid):
        with self.lock:
            record = self.jobs.get(jid)
            if record is None or record['owner'] != owner:
                raise HTTPException(404, '执行任务不存在')
            return json.loads(json.dumps({k: v for k, v in record.items() if k not in {'owner', 'request_sha256'}}))

    def cancel(self, owner, jid):
        with self.lock:
            self.view(owner, jid)
            record = self.jobs[jid]
            if record['status'] not in TERMINAL:
                self.cancelled[jid].set()
                record['status'] = 'cancel_requested'
                self.persist(record)
            return self.view(owner, jid)

    def execute(self, jid, value):
        record, folder = self.jobs[jid], self.root / jid
        event = self.cancelled[jid]
        def cancelled():
            return event.is_set() or time.time() >= record['expires_at']
        try:
            with self.lock:
                if cancelled():
                    record['status'] = 'cancelled' if event.is_set() else 'expired'
                    return
                record['status'] = 'running'
                self.persist(record)
            task = get_task(value['project'])
            runner = self.runner_factory(task.project)
            runner.cancelled = cancelled
            probe = runner.preflight(folder / 'isolation')
            if not probe.get('isolation_ready'):
                raise IsolationBlocked(probe.get('reason', '隔离探针未通过'))
            if cancelled():
                raise IsolationBlocked('执行已取消或时限到达')
            if value['kind'] == 'probe':
                result = probe
            else:
                if probe.get('image') != value['image']:
                    raise IsolationBlocked('执行镜像与客户端探针版本不一致，请重新检查')
                work = task.prepare(folder / 'workspace')
                (work / task.source).write_bytes(value['source'].encode('utf-8'))
                task.verify(work, candidate=True)
                if code_version(work, task.profile['source_paths']) != value['code_version']:
                    raise IsolationBlocked('提交源码与候选版本不一致')
                result = runner.run(work, folder / 'test', phase=value['phase'],
                                    timeout=max(.01, min(value['test_seconds'], record['expires_at'] - time.time())),
                                    expected_version=value['code_version'])
                if (result.get('code_version') != value['code_version'] or
                        result.get('test_suite_version') != value['test_suite_version'] or
                        result.get('phase') != value['phase']):
                    raise IsolationBlocked('执行返回证据版本不一致')
            with self.lock:
                record.update(result=result, image=probe.get('image'))
        except Exception as exc:
            # Keep full diagnostics locally; never export exception paths or secrets.
            from .diagnostics import record_exception
            record_exception(folder, exc)
            with self.lock:
                record.update(status='failed', reason=str(exc) if isinstance(exc, IsolationBlocked) else '执行服务异常，请联系管理员',
                              error_type=type(exc).__name__)
        finally:
            with self.lock:
                if event.is_set():
                    record.update(status='cancelled', reason='用户取消执行', result=None)
                elif time.time() >= record['expires_at']:
                    record.update(status='expired', reason='队列等待与执行总时限到达', result=None)
                elif record['status'] == 'running' and record['result'] is not None:
                    record['status'] = 'completed'
                record['finished_at'] = time.time()
                self.persist(record)

    def evidence(self, owner, jid):
        record = self.view(owner, jid)
        if record['status'] != 'completed':
            raise HTTPException(409, '执行未完成，没有可用于验收的结果')
        folder, files, total = self.root / jid, {}, 0
        for name in EVIDENCE_NAMES:
            path = folder / name
            if not path.is_file():
                continue
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise HTTPException(500, '证据目录包含链接')
            total += path.stat().st_size
            if total > MAX_EVIDENCE:
                raise HTTPException(500, '执行证据超过传输上限')
            raw = path.read_bytes()
            files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'base64': base64.b64encode(raw).decode('ascii')}
        return {'protocol': PROTOCOL, 'job': record, 'files': files}


def create_execution_app(root=None, tokens=None, *, concurrency=1, runner_factory=SplitDockerRunner):
    tokens_path = None
    if tokens is None:
        tokens_path = Path(os.environ['CODE_FIX_EXECUTION_TOKENS_FILE'])
        tokens = json.loads(tokens_path.read_text(encoding='utf-8'))
    jobs = ExecutionJobs(root or os.environ.get('CODE_FIX_EXECUTION_ROOT', 'runs/remote-execution'),
                         tokens, concurrency=concurrency, runner_factory=runner_factory)
    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        yield
        jobs.close()
    app = FastAPI(title='Code Fix isolation execution', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs

    @app.middleware('http')
    async def authenticate(request, call_next):
        from fastapi.responses import JSONResponse
        token = request.headers.get('authorization', '')
        if not token.startswith('Bearer ') or not 32 <= len(token[7:]) <= 256:
            return JSONResponse({'detail': '需要执行服务访问码'}, status_code=401)
        measured = hashlib.sha256(token[7:].encode()).hexdigest()
        try:
            current = json.loads(tokens_path.read_text(encoding='utf-8')) if tokens_path else jobs.tokens
            if not isinstance(current, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in current.items()):
                raise ValueError('Invalid access registry')
        except (ValueError, OSError):
            return JSONResponse({'detail': '执行服务访问配置暂不可用'}, status_code=503)
        owner = next((name for name, expected in current.items() if secrets.compare_digest(measured, expected)), None)
        if owner is None:
            return JSONResponse({'detail': '执行服务访问码无效'}, status_code=401)
        request.state.owner = owner
        return await call_next(request)

    @app.get('/v1/health')
    def health():
        return {'protocol': PROTOCOL, 'concurrency': jobs.concurrency, 'model_requests': False,
                'image': os.environ.get('CODE_FIX_DOCKER_IMAGE'), 'status': 'available',
                'server_build_sha256': jobs.build_sha256}

    @app.post('/v1/jobs', status_code=202)
    async def submit(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise HTTPException(413, '执行请求过大')
        try:
            value = json.loads(body)
            validate_job(value)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            raise HTTPException(422, '执行参数无效、题目尚未准入或测试版本不一致') from None
        return jobs.submit(request.state.owner, value)

    @app.get('/v1/jobs/{jid}')
    def get_job(jid: str, request: Request):
        return jobs.view(request.state.owner, jid)

    @app.post('/v1/jobs/{jid}/cancel')
    def cancel_job(jid: str, request: Request):
        return jobs.cancel(request.state.owner, jid)

    @app.get('/v1/jobs/{jid}/evidence')
    def get_evidence(jid: str, request: Request):
        return jobs.evidence(request.state.owner, jid)
    return app
