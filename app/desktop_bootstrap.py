"""Private local backend spawned by Electron; no model requests at startup."""
import json
import os
from pathlib import Path
import socket


def main():
    from .personal import configure_runtime
    root = Path(__file__).resolve().parents[1]
    configure_runtime(root, json.loads(os.environ['CODE_FIX_DESKTOP_SETTINGS']))
    from .api import create_app
    import uvicorn
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        print(json.dumps({'desktop_port': listener.getsockname()[1]}), flush=True)
        uvicorn.Server(uvicorn.Config(create_app(), log_level='warning', proxy_headers=False)).run(sockets=[listener])


if __name__ == '__main__':
    main()
