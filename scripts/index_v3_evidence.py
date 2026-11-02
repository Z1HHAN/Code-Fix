"""Index reviewed local artifacts, never label offline checks as model success."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
folder = root / 'evidence/v3'
suite = ET.parse(folder / 'regression.junit.xml').getroot().find('testsuite')
files = {str(p.relative_to(folder)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(folder.iterdir()) if p.is_file() and p.name != 'index.json'}
sources = {}
for directory in ('app', 'c_runner', 'configs', 'prompts', 'ui/app', 'docker', 'tests'):
    for path in sorted((root / directory).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            sources[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
value = {
    'date': '2026-11-02', 'timezone': 'Asia/Shanghai', 'complete_project_accepted': False,
    'paid_calls_authorized': False, 'real_model_calls': 0,
    'model_validation': 'pending_real_model', 'candidate_execution_validation': 'pending_docker_environment',
    'baseline_validation': 'real_cli_http_browser_and_pytest',
    'pytest': {'tests': int(suite.attrib['tests']), 'failures': int(suite.attrib['failures']),
               'errors': int(suite.attrib['errors']), 'skipped': int(suite.attrib['skipped']),
               'passed': int(suite.attrib['tests'])-int(suite.attrib['failures'])-int(suite.attrib['errors'])-int(suite.attrib['skipped'])},
    'browser_run_id': 'run-3255f2d3640c40e88289c84e490414ac',
    'archive_note': 'Complete original-baseline evidence only; not a model-repair result',
    'file_sha256': files, 'tested_source_sha256': sources,
    'hash_note': 'Actual tested working-tree bytes; text EOL conversion can change hashes in another checkout.',
    'committed_module_versions': {tag: subprocess.check_output(['git','rev-list','-n','1',tag], cwd=root, text=True).strip()
                                  for tag in ('v1.0-cli-baseline','dev-v3-agent-contracts','dev-v3-workflow-offline')}
}
(folder / 'index.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(value['pytest']))
print('Indexed', len(files), 'artifacts and', len(sources), 'source files')
