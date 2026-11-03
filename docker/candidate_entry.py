"""Container harness; host retrieves bounded tmpfs reports via Docker API."""
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
    print('CODEFIX_READY', flush=True)
    # Keep tmpfs mounted until the host has copied artifacts, or closes stdin.
    # A marker is only a readiness hint, never sufficient acceptance evidence.
    sys.stdin.readline()
raise SystemExit(exit_code)
