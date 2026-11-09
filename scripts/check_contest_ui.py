"""Windows Edge configuration/comparison check; tasks are only created, never started."""
from pathlib import Path
import os
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from app.api import create_app


def main():
    os.chdir(ROOT)
    os.environ['CODE_FIX_CONFIG_BROWSER_URL'] = 'http://127.0.0.1:8783'
    os.environ.setdefault('CODE_FIX_CONFIG_BROWSER_OUTPUT', 'runs/contest-browser')
    server = uvicorn.Server(uvicorn.Config(create_app('runs/contest-browser-store'),
                                         host='127.0.0.1', port=8783, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError('Temporary server exited before readiness')
            time.sleep(.05)
        if not server.started:
            raise RuntimeError('Temporary server did not become ready')
        result = subprocess.run(['C:/Program Files/nodejs/node.exe', 'tests/browser_contest_delivery.cjs'], check=False)
        return result.returncode
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == '__main__':
    raise SystemExit(main())
