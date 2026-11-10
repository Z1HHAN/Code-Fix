"""Disposable read-only viewer for received B histories, never a task executor."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'app/api.py').is_file())
sys.path.insert(0, str(ROOT))
import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse
from app.api import create_app

SOURCE = ROOT / 'evidence/contest-strategies-config-comparison'
OUT = ROOT / 'evidence/history-replay-label'
STORE = ROOT / 'runs' / ('b-real-replay-store-' + uuid.uuid4().hex[:10])

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def extended(path):
    return Path('\\\\?\\' + str(path.resolve())) if os.name == 'nt' else path

def main():
    os.chdir(ROOT)
    OUT.mkdir(exist_ok=True)
    app = create_app(STORE)
    store = app.state.service.store
    raw_requests = []
    # Defense independent of the HTTP read-only guard below.
    def forbidden(*args, **kwargs):
        raise AssertionError('Received-history viewer must never execute a task')
    app.state.service.execute = forbidden
    app.state.service.run_claimed = forbidden
    for config in (SOURCE / 'configs').glob('*.json'):
        payload = read(config)
        with store.connect() as db:
            db.execute('INSERT INTO configurations VALUES (?,?)',
                       (payload['config_id'], json.dumps(payload, ensure_ascii=False)))
    for folder in sorted((SOURCE / 'runs').iterdir()):
        if not folder.is_dir():
            continue
        report = read(folder / 'report-from-api.json')
        run = report['run']
        shutil.copytree(extended(folder), extended(STORE / folder.name))
        with store.connect() as db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',
                       (run['run_id'], json.dumps(run['task'], ensure_ascii=False),
                        hashlib.sha256(json.dumps(run['task'], sort_keys=True).encode()).hexdigest(),
                        None, run['status'], run['created_at'], run['updated_at'],
                        json.dumps(run['result'], ensure_ascii=False), run['control']))
            for event in report['events']:
                db.execute('INSERT INTO events VALUES (?,?,?,?)',
                           (event['sequence'], run['run_id'], event['created_at'],
                            json.dumps(event, ensure_ascii=False)))
    comparison = read(SOURCE / 'comparison/comparison.json')
    with store.connect() as db:
        db.execute('INSERT INTO comparisons VALUES (?,?)',
                   (comparison['comparison_id'], json.dumps(comparison, ensure_ascii=False)))

    @app.middleware('http')
    async def history_only(request, call_next):
        raw_requests.append({'method': request.method, 'path': request.url.path})
        if request.method not in {'GET', 'HEAD'}:
            return JSONResponse({'detail': 'B真实历史只读核对，不允许创建或启动任务'}, status_code=405)
        if request.url.path == '/api/v3/budget':
            return JSONResponse(read(SOURCE / 'ledger/budget-after.json'))
        return await call_next(request)

    os.environ['CODE_FIX_B_REPLAY_URL'] = 'http://127.0.0.1:8784'
    os.environ['CODE_FIX_B_REPLAY_OUTPUT'] = str(OUT)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8784, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError('Read-only review server exited')
            time.sleep(.05)
        if not server.started:
            raise RuntimeError('Read-only review server readiness timeout')
        result = subprocess.run(['C:/Program Files/nodejs/node.exe',
                                 str(Path(__file__).with_name('browser_b_replay.cjs'))], check=False)
        with store.connect() as db:
            states = db.execute('SELECT run_id,status FROM runs ORDER BY run_id').fetchall()
            assert len(states) == 6 and all(row['status'] == 'success' for row in states)
            assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 210
        assert not [r for r in raw_requests if r['method'] not in {'GET', 'HEAD'}]
        (OUT / 'server-verification.json').write_text(json.dumps({
            'scope': 'A-side browser inspection of received B real histories; no UI task initiation',
            'copied_store': str(STORE), 'requests': raw_requests,
            'mutations': 0, 'original_runs': 6, 'original_events': 210,
            'states_unchanged': True, 'browser_exit_code': result.returncode,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        return result.returncode
    finally:
        server.should_exit = True
        thread.join(timeout=10)

if __name__ == '__main__':
    raise SystemExit(main())
