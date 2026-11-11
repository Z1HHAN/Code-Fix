"""Boot an isolated copy with fake credentials; validate UI/config without calls."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.request import urlopen, Request, build_opener, ProxyHandler


def check(python, backend):
    assert not (backend / 'configs/model_profiles.json').exists(), 'Bundle must not ship personal profiles'
    assert not (backend / 'configs/agents.json').exists(), 'Bundle must not ship team accounts'
    with tempfile.TemporaryDirectory(prefix='codefix-desktop-check-') as temp:
        folder = Path(temp)
        runtime = folder / 'runtime'
        shutil.copytree(backend, runtime)
        value = dict(base_url='https://desktop-test.example/v1', model='offline-test-model',
                     max_output_tokens=2048, token_parameter='max_tokens', price_version='test-only')
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PATH', 'TEMP', 'TMP'}}
        env.update(CODE_FIX_PERSONAL_MODE='1', CODE_FIX_PERSONAL_BASE_URL=value['base_url'],
                   CODE_FIX_PERSONAL_KEY='fake-offline-only-key', CODE_FIX_PERSONAL_APPROVED='1',
                   CODE_FIX_LIVE_APPROVED='1', CODE_FIX_EXECUTION_APPROVED='1', CODE_FIX_PERSONAL_BUDGET='usage',
                   CODE_FIX_EXECUTION_URL='http://127.0.0.1:17880', CODE_FIX_EXECUTION_SSH_TUNNEL='1',
                   CODE_FIX_EXECUTION_TOKEN='t'*40, CODE_FIX_RUN_ROOT=str(folder / 'runs'),
                   CODE_FIX_DESKTOP_SETTINGS=json.dumps(value), PYTHONUTF8='1')
        process = subprocess.Popen([str(python), '-I', '-B', '-X', 'utf8', str(runtime / 'desktop-launch.py')],
                                   cwd=runtime, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            port = json.loads(lines.get(timeout=20))['desktop_port']
            base = f'http://127.0.0.1:{port}'
            opener = build_opener(ProxyHandler({}))
            def read(path):
                with opener.open(base + path, timeout=15) as response:
                    return response.read()
            for _ in range(60):
                try:
                    session = json.loads(read('/api/v3/session'))
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise AssertionError('Desktop HTTP service did not start')
            catalog = json.loads(read('/api/v3/configuration/catalog'))
            assert len(catalog['profiles']) == 1 and catalog['profiles'][0]['provider'] == 'personal'
            assert catalog['profiles'][0]['configured'] is True
            assert catalog['accounts'] == [{'account_id': 'personal', 'provider': 'personal', 'display_name': '我的模型'}]
            health = json.loads(read('/api/v3/health'))
            assert health['model_configured'] is True and health['execution_mode'] == 'remote-docker'
            assert not health['blockers'], health['blockers']
            budget = json.loads(read('/api/v3/budget'))
            assert budget['personal'] and budget['monetary_budget_enabled'] is False
            draft = {'strategy': 'dynamic', 'parameters': {'max_rounds': 3, 'max_calls': 12, 'total_seconds': 300},
                     'bindings': {role: 'personal_model' for role in ('locator', 'repairer', 'detector', 'scheduler')}}
            request = Request(base+'/api/v3/configurations', data=json.dumps(draft).encode(),
                              headers={'Content-Type': 'application/json', 'X-Codefix-Token': session['token']})
            with opener.open(request, timeout=15) as response:
                saved = json.loads(response.read())
            assert saved['config_id'].startswith('cfg-')
            assert 'fake-offline-only-key' not in json.dumps(saved)
            assert '模型与配置' in read('/').decode('utf-8')
            assert json.loads(read('/api/v3/tasks')) == []
            print('Bundled backend: startup, personal catalog, no Docker dependency, unpriced budget, immutable config and UI passed; no model or cloud requests.')
        finally:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    args = parser.parse_args()
    check(args.python.resolve(), args.backend.resolve())
