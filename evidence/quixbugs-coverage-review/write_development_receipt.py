"""Record observed offline checks and source bytes; never dispatch execution."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FILES = ('app/api.py', 'app/comparisons.py', 'ui/app/comparisons.js',
         'tests/test_comparisons.py', 'tests/browser_contest_delivery.cjs',
         'scripts/check_contest_ui.py', 'configs/tasks/catalog-v3.json')
checks = []
for filename in ('full-coverage-tests.xml', 'api-regression.xml'):
    suites = ElementTree.parse(OUT / filename).getroot().findall('testsuite')
    totals = {name: sum(int(s.get(name, '0')) for s in suites)
              for name in ('tests', 'failures', 'errors', 'skipped')}
    assert totals['failures'] == totals['errors'] == 0
    checks.append({'junit': filename, **totals})
browser = json.loads((OUT / 'browser/result.json').read_text(encoding='utf-8'))
assert browser['passed'] and len(browser['default_projects']) == 40
assert all(browser['summary'][s]['planned'] == browser['summary'][s]['not_run'] == 40
           for s in ('fixed', 'dynamic'))
assert not any(url.endswith('/start') for url in browser['writes'])
receipt = {
    'record': '076', 'actor': 'assistant_local_development_not_C_signoff',
    'recorded_at': datetime.now().astimezone().isoformat(),
    'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
    'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'code_identity': 'base_commit plus listed working-tree SHA256; not a clean-commit acceptance',
    'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES},
    'checks': checks,
    'observed_node_result': {'tests': 60, 'passed': 60, 'failed': 0,
                             'source': 'actual exec_command stdout, no raw TAP file archived'},
    'browser_result': 'browser/result.json',
    'commands': [
        r'.\runs\v3-venv\Scripts\python.exe -B -X utf8 -m pytest -q tests/test_comparisons.py tests/test_contest_delivery.py -p no:cacheprovider --basetemp=runs/quixbugs-full-coverage-tests --junitxml=evidence/quixbugs-coverage-review/full-coverage-tests.xml',
        r'.\runs\v3-venv\Scripts\python.exe -B -X utf8 -m pytest -q tests/test_api_v3.py -p no:cacheprovider --basetemp=runs/quixbugs-full-coverage-api --junitxml=evidence/quixbugs-coverage-review/api-regression.xml',
        'node --test tests/ui_prototype.test.cjs tests/ui_budget.test.cjs tests/ui_trace.test.cjs tests/ui_architecture.test.cjs tests/ui_polling.test.cjs',
        r"$env:CODE_FIX_CONFIG_BROWSER_OUTPUT='evidence/quixbugs-coverage-review/browser'; $env:CODE_FIX_CONFIG_BROWSER_STORE='runs/quixbugs-full-coverage-browser-store-2'; .\runs\v3-venv\Scripts\python.exe -B -X utf8 scripts/check_contest_ui.py",
    ],
    'limits': {'comparison_post_bytes': 131072, 'task_post_bytes': 8192},
    'model_requests': 0, 'docker_execution': False, 'full_C_entry_rerun': False,
    'historical_B_originals_modified': False, 'main_merged': False,
}
(OUT / 'development-verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
files = [p for p in OUT.rglob('*') if p.is_file() and 'profile' not in p.relative_to(OUT).parts
         and '__pycache__' not in p.relative_to(OUT).parts and p.name != 'SHA256SUMS.txt']
(OUT / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(OUT).as_posix()+'\n'
    for p in sorted(files)), encoding='utf-8')
print(json.dumps({'junit': checks, 'checksummed_files': len(files), 'model_requests': 0}, ensure_ascii=False))
