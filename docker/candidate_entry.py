"""Trusted container harness; reports leave bounded tmpfs through stdout only."""
import base64
import json
from pathlib import Path
import runpy
import sys

Path('/out/pytest.ini').write_text('[pytest]\n', encoding='utf-8')
test_paths = sys.argv[1:] or ['tests']
sys.argv = ['/entry.py', '/out/pytest_events.json', '/out/junit.xml', '/out/pytest.ini', *test_paths]
exit_code = 2
try:
    runpy.run_path('/entry.py', run_name='__main__')
except SystemExit as exc:
    exit_code = exc.code if type(exc.code) is int else 2
finally:
    bundle = {}
    for name in ('junit.xml', 'pytest_events.json'):
        path = Path('/out') / name
        if path.is_file() and path.stat().st_size <= 2_097_152:
            bundle[name] = base64.b64encode(path.read_bytes()).decode('ascii')
    print('CODEFIX_REPORT:' + base64.b64encode(json.dumps(bundle).encode()).decode('ascii'), flush=True)
raise SystemExit(exit_code)
