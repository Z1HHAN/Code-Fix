"""Local FastAPI service. No credentials, paths or commands accepted from UI."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .run_store import StateConflict
from .service import TaskService

ROOT = Path(__file__).resolve().parents[1]


def create_app(root=None):
    service = TaskService(root or os.environ.get('CODE_FIX_RUN_ROOT'))
    token = secrets.token_urlsafe(32)
    workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='codefix')

    @asynccontextmanager
    async def lifespan(app):
        yield
        workers.shutdown(wait=True)

    app = FastAPI(title='验链 Code Fix V3', version='0.3.0-dev', lifespan=lifespan)
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
            if not secrets.compare_digest(request.headers.get('x-codefix-token', ''), token):
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

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': '任务不存在'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.get('/api/v3/session')
    def session():
        return {'token': token, 'deployment': 'local-only', 'version': 'v3-development'}

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
        except ValueError:
            raise HTTPException(422, '无效JSON') from None
        return service.store.create(value, request.headers.get('idempotency-key'))

    @app.get('/api/v3/tasks')
    def tasks():
        return service.store.list()

    @app.get('/api/v3/tasks/{run_id}')
    def task(run_id: str):
        return service.store.get(run_id)

    @app.post('/api/v3/tasks/{run_id}/start', status_code=202)
    def start(run_id: str):
        service.store.get(run_id)
        service.store.claim(run_id)
        try:
            workers.submit(service.run_claimed, run_id)
        except RuntimeError:
            service.store.finish(run_id, 'interrupted', {'final_result': 'not_run', 'reason': '服务正在关闭，未启动worker'})
            raise HTTPException(503, '服务正在关闭') from None
        return service.store.get(run_id)

    @app.post('/api/v3/tasks/{run_id}/control')
    async def control(run_id: str, request: Request):
        import json
        raw = await limited_body(request, 1024)
        try:
            value = json.loads(raw)
        except ValueError:
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
        from .security import normalise_relpath
        service.store.get(run_id)
        relative = normalise_relpath(path)
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
        import hashlib
        import io
        import json
        import zipfile
        folder = service.store.directory(run_id)
        buffer = io.BytesIO()
        manifest = {}
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            encoded = json.dumps(service.report(run_id), ensure_ascii=False, indent=2).encode()
            archive.writestr('report.json', encoded)
            manifest['report.json'] = hashlib.sha256(encoded).hexdigest()
            total = len(encoded)
            for path in sorted(folder.rglob('*')):
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise StateConflict('拒绝导出含链接的运行目录')
                if not path.is_file() or path.name == 'report.json':
                    continue
                total += path.stat().st_size
                if total > 64 * 1024 * 1024:
                    raise StateConflict('证据包超过64MiB，请由操作员分包归档')
                data = path.read_bytes()
                name = path.relative_to(folder).as_posix()
                archive.writestr(name, data)
                manifest[name] = hashlib.sha256(data).hexdigest()
            archive.writestr('sha256.json', json.dumps(manifest, indent=2))
        return Response(buffer.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{run_id}.zip"'})

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'ui/app/index.html')

    app.mount('/assets', StaticFiles(directory=ROOT / 'ui/app'), name='assets')
    return app
