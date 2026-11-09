"""Local FastAPI service. No credentials, paths or commands accepted from UI."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets
from threading import BoundedSemaphore

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .run_store import StateConflict
from .service import TaskService
from .errors import InputValidationError, RunNotFound
from .version import SERVICE_VERSION, REQUIREMENTS_BASELINE

ROOT = Path(__file__).resolve().parents[1]


def create_app(root=None):
    service = TaskService(root or os.environ.get('CODE_FIX_RUN_ROOT'))
    token = secrets.token_urlsafe(32)
    workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='codefix')
    capacity = BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        yield
        workers.shutdown(wait=True)

    app = FastAPI(title='验链 Code Fix V3', version=SERVICE_VERSION, lifespan=lifespan)
    app.state.service = service

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        # Loopback-only is enforced at launch; also reject DNS rebinding and
        # cross-origin requests, including reads of task data.
        if request.url.hostname not in {'127.0.0.1', 'localhost', 'testserver'}:
            return JSONResponse({'detail': '仅供本机访问'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            provided = request.headers.get('x-codefix-token', '')
            if not provided.isascii() or not secrets.compare_digest(provided, token):
                return JSONResponse({'detail': '会话令牌无效；刷新页面后重试'}, status_code=403)
            size = request.headers.get('content-length')
            if size and (not size.isdecimal() or int(size) > 8192):
                return JSONResponse({'detail': '请求体过大'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(StateConflict)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(RunNotFound)
    async def missing(request, exc):
        return JSONResponse({'detail': '任务不存在'}, status_code=404)

    @app.exception_handler(InputValidationError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.get('/api/v3/session')
    def session():
        return {'token': token, 'deployment': 'local-only', 'version': SERVICE_VERSION,
                'requirements_baseline': REQUIREMENTS_BASELINE}

    @app.get('/api/v3/health')
    def health():
        from .readiness import readiness
        return readiness()

    @app.get('/api/v3/budget')
    def budget():
        from .siliconflow import budget_status
        return budget_status(service.store.root)

    @app.get('/api/v3/projects')
    def projects():
        from .tasks_v3 import list_tasks
        return list_tasks()

    from .configuration import ConfigurationStore
    configurations = ConfigurationStore(service.store)

    @app.get('/api/v3/configuration/catalog')
    def configuration_catalog():
        return configurations.catalog()

    @app.get('/api/v3/configurations/{config_id}')
    def configuration_get(config_id: str):
        return configurations.get(config_id)

    @app.post('/api/v3/configurations/validate')
    @app.post('/api/v3/configurations', status_code=201)
    async def configuration_save(request: Request):
        import json
        from .config import ConfigError
        try:
            value = json.loads(await limited_body(request, 8192))
            return configurations.validate(value) if request.url.path.endswith('/validate') else configurations.save(value)
        except (ValueError, ConfigError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from None

    async def limited_body(request, limit):
        result = bytearray()
        async for chunk in request.stream():
            if len(result) + len(chunk) > limit:
                raise HTTPException(413, '请求体过大')
            result.extend(chunk)
        return bytes(result)

    @app.post('/api/v3/tasks', status_code=201)
    async def create(request: Request):
        body = await limited_body(request, 8192)
        import json
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(422, '无效JSON') from None
        return service.store.create(value, request.headers.get('idempotency-key'))

    @app.get('/api/v3/tasks')
    def tasks(include_archived: bool = False):
        return service.store.list(include_archived=include_archived)

    @app.post('/api/v3/tasks/{run_id}/archive')
    def archive(run_id: str):
        return service.store.set_archived(run_id, True)

    @app.post('/api/v3/tasks/{run_id}/restore')
    def restore(run_id: str):
        return service.store.set_archived(run_id, False)

    @app.get('/api/v3/tasks/{run_id}')
    def task(run_id: str):
        return service.store.get(run_id)

    @app.post('/api/v3/tasks/{run_id}/start', status_code=202)
    def start(run_id: str):
        run = service.store.get(run_id)
        if run['status'] != 'created' or run['archived_at']:
            raise StateConflict('仅未归档的新建任务可以启动；请先恢复已归档任务')
        if not capacity.acquire(blocking=False):
            raise HTTPException(503, '执行容量已满，请稍后重试；任务尚未启动')
        try:
            service.store.claim(run_id)
            try:
                future = workers.submit(service.run_claimed, run_id)
            except RuntimeError:
                service.store.finish_if_running(run_id, 'interrupted', {'final_result': 'not_run', 'reason': '服务正在关闭，未启动worker'})
                raise HTTPException(503, '服务正在关闭') from None
        except BaseException:
            capacity.release()
            raise
        future.add_done_callback(lambda completed: capacity.release())
        return service.store.get(run_id)

    @app.post('/api/v3/tasks/{run_id}/control')
    async def control(run_id: str, request: Request):
        import json
        raw = await limited_body(request, 1024)
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(422, '无效JSON') from None
        if not isinstance(value, dict) or set(value) != {'action'}:
            raise HTTPException(422, '仅接受action')
        service.store.request_control(run_id, value['action'])
        return service.store.get(run_id)

    @app.get('/api/v3/tasks/{run_id}/events')
    def events(run_id: str, after_sequence: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)):
        return service.store.events(run_id, after_sequence, limit)

    @app.get('/api/v3/tasks/{run_id}/report')
    def report(run_id: str):
        return service.report(run_id)

    @app.get('/api/v3/tasks/{run_id}/artifact')
    def artifact(run_id: str, path: str):
        from .security import normalise_relpath, PermissionViolation
        service.store.get(run_id)
        try:
            relative = normalise_relpath(path)
        except PermissionViolation as exc:
            raise HTTPException(422, str(exc)) from None
        folder = service.store.directory(run_id)
        target = folder
        for part in relative.split('/'):
            target = target / part
            if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
                raise HTTPException(403, '禁止链接产物')
        if not target.resolve().is_relative_to(folder.resolve()) or not target.is_file():
            raise HTTPException(404, '产物不存在')
        if target.stat().st_size > 1_048_576:
            raise HTTPException(413, '产物过大，请下载证据包')
        return Response(target.read_bytes(), media_type='text/plain; charset=utf-8')

    @app.get('/api/v3/tasks/{run_id}/bundle')
    def bundle(run_id: str):
        run = service.store.get(run_id)
        if run['status'] in {'created', 'running'}:
            raise StateConflict('任务结束后才能导出完整证据包')
        from .bundle import build_bundle, chunks
        from fastapi.responses import StreamingResponse
        from starlette.background import BackgroundTask
        buffer = build_bundle(service.store.directory(run_id), service.report(run_id))
        return StreamingResponse(chunks(buffer), media_type='application/zip',
                                 background=BackgroundTask(buffer.close),
                                 headers={'Content-Disposition': f'attachment; filename="{run_id}.zip"'})

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'ui/app/index.html')

    app.mount('/assets', StaticFiles(directory=ROOT / 'ui/app'), name='assets')
    return app
