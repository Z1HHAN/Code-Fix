"""Stage A read-only verification. Never execute model/candidate/public code.

Writes a NEW JSON only when --output is supplied. Existing evidence is never
overwritten. The earlier document-only governance result remains historical.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
HUB = ROOT / 'docs/2026-11-03_项目持续维护'
parser = argparse.ArgumentParser()
parser.add_argument('--output')
args = parser.parse_args()
output = (ROOT / args.output).resolve() if args.output else None
if output is not None and (not output.is_relative_to(ROOT) or output.exists()):
    raise ValueError('输出必须是仓库内的新文件，不可覆盖证据')
sha = lambda raw: hashlib.sha256(raw).hexdigest()
errors = []

def run(args):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    return {'command': args, 'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}

def fail(kind, detail):
    errors.append({'kind': kind, 'detail': detail})

base = json.loads((HUB / '核验资料/治理前文件指纹.json').read_text(encoding='utf-8'))
mapping = {r['原路径']: r['实际保存路径'] for r in csv.DictReader(
    (HUB / '文件迁移与合并映射.csv').open(encoding='utf-8', newline=''))}
# Explicitly authorized runtime changes in A-赛事底座-004. No evidence/V3/C
# source is exempted. Report before/after hashes rather than silently dropping it.
changed = {'app/agents_v3.py', 'app/api.py', 'app/live_model.py', 'app/readiness.py',
           'app/role_config.py', 'app/run_store.py', 'app/service.py', 'app/workflow_v3.py',
           'tests/test_agents_v3.py', '.gitattributes'}
preserved, authorized_changes = [], []
for original, digest in base['hashes'].items():
    target = ROOT / mapping.get(original, original)
    if not target.is_file():
        fail('original_missing', original)
        continue
    immutable = not original.endswith('.md') or original == 'docs/01_需求与范围/开发要求V3.0.md'
    immutable |= original.startswith(('evidence/', 'docs/99_历史归档/', 'docs/integration/')) and original not in {
        'docs/99_历史归档/历史归档索引.md', 'docs/99_历史归档/固定位置历史材料索引.md'}
    actual = sha(target.read_bytes())
    if original in changed:
        authorized_changes.append({'path': original, 'governance_baseline_sha256': digest, 'current_raw_sha256': actual})
    elif immutable:
        if actual != digest:
            fail('protected_bytes_changed', original)
        else:
            preserved.append(original)

states = {}
for ident, body in re.findall(r'^## (T-\d{3})[^\n]*\n(.*?)(?=^## |\Z)',
        (HUB / '当前开发任务台账.md').read_text(encoding='utf-8'), re.M | re.S):
    fields = dict(re.findall(r'^\| ([^|]+) \| ([^|]*) \|$', body, re.M))
    states[ident] = {k: fields.get(k) for k in ('实现状态', '测试状态', '独立验收状态')}
if len(states) != 46:
    fail('task_count', len(states))
for ident in ('T-021', 'T-022', 'T-054', 'T-057', 'T-058', 'T-059'):
    if states.get(ident) != {'实现状态': '已实现', '测试状态': '通过', '独立验收状态': '未运行'}:
        fail('scoped_component_status', ident)
pending = set(re.findall(r'\[(T-\d{3}) ', (HUB / '当前待办索引.md').read_text(encoding='utf-8')))
done = set(re.findall(r'\[(T-\d{3}) ', (HUB / '已完成任务索引.md').read_text(encoding='utf-8')))
if pending & done or pending | done != set(states) or len(done) != 26 or len(pending) != 20:
    fail('task_partition', {'pending': sorted(pending), 'done': sorted(done)})

links, anchors = 0, 0
docs = list(HUB.rglob('*.md')) + [ROOT / p for p in (
    'README.md', 'AGENTS.md', 'CHANGELOG.md', '.github/pull_request_template.md',
    'c_runner/README.md', 'c_tests/README.md', 'c_fixtures/README.md',
    'task_assets/leap_legacy/README.md', 'task_assets/leap_legacy/队长今日执行与台账.md', 'ui/prototype/README.md')]
for doc in docs:
    text = re.sub(r'```.*?```', '', doc.read_text(encoding='utf-8-sig'), flags=re.S)
    for match in re.finditer(r'!?\[[^\]\n]*\]\((<[^>\n]+>|[^)\s\n]+)\)', text):
        url = match[1].strip('<>')
        if re.match(r'^(?:[a-zA-Z][\w+.-]*:|/)', url):
            continue
        path, _, anchor = url.partition('#')
        target = (doc.parent / unquote(path)).resolve() if path else doc
        links += 1
        # This report may link to its own not-yet-created output; every other
        # linked artifact must already exist. The exclusive write below is final.
        if not target.is_relative_to(ROOT) or (not target.exists() and target != output):
            fail('broken_link', {'path': doc.relative_to(ROOT).as_posix(), 'url': url})
        elif anchor and target.suffix.lower() == '.md':
            anchors += 1
            contents = target.read_text(encoding='utf-8-sig')
            ids = set(re.findall(r'<a\s+id="([^"]+)"', contents))
            ids |= {re.sub(r'[^\w\s\-\u4e00-\u9fff]', '', h.lower()).replace(' ', '-')
                    for h in re.findall(r'^#{1,6} (.+)$', contents, re.M)}
            if unquote(anchor) not in ids:
                fail('missing_anchor', url)

results = {}
for name in ('first-specialist.junit.xml', 'intermediate-platform.junit.xml', 'final-platform.junit.xml',
             'reviewed-platform.junit.xml'):
    suites = ET.parse(HERE / name).getroot().iter('testsuite')
    counts = {k: 0 for k in ('tests', 'failures', 'errors', 'skipped')}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.get(key, 0))
    counts['passed'] = counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped']
    results[name] = counts
if results['reviewed-platform.junit.xml'] != {'tests': 222, 'failures': 0, 'errors': 0, 'skipped': 1, 'passed': 221}:
    fail('final_platform_result', results)

commands = [run([sys.executable, '-B', '-X', 'utf8', '-m', 'app.cli', 'validate-config']),
            run([sys.executable, '-B', '-X', 'utf8', '-m', 'app.cli', 'doctor']),
            run(['node', '--test', 'tests/ui_prototype.test.cjs'])]
for result in commands:
    if result['exit_code']:
        fail('command_failed', result)

source_paths = set()
for directory in ('app', 'tests', 'c_runner', 'c_tests', 'configs', 'prompts'):
    source_paths.update(p for p in (ROOT / directory).rglob('*')
                        if p.is_file() and p.suffix in {'.py', '.json', '.md', '.cjs'})
source_paths.update(ROOT / p for p in ('requirements-lock.txt', 'requirements.txt', 'pytest.ini'))
source_hashes = {p.relative_to(ROOT).as_posix(): {
    'raw_sha256': sha(p.read_bytes()),
    'lf_normalized_sha256': sha(p.read_bytes().replace(b'\r\n', b'\n'))}
    for p in sorted(source_paths) if p.exists()}
artifact_hashes = {p.relative_to(HERE).as_posix(): sha(p.read_bytes()) for p in sorted(HERE.rglob('*'))
                   if p.is_file() and p.name != 'verification.json'}
result = {
    'scope': 'Stage A OFFLINE component acceptance only; no live API, Docker or public/candidate execution',
    'date': '2026-11-03', 'development_base_commit': '399b046b947849120b91a4541e5b32fd60c69f09',
    'git_head_at_check': run(['git', 'rev-parse', 'HEAD'])['stdout'].strip(),
    'branch_at_check': run(['git', 'branch', '--show-current'])['stdout'].strip(),
    'tested_identity': 'source_hashes; current worktree, not a B/C independent signature',
    'final_pytest_command': 'runs/v3-venv/Scripts/python.exe -B -X utf8 -m pytest -q --basetemp=runs/ca6 -o cache_dir=runs/contest-core/cache --junitxml=runs/contest-core/reviewed-platform.junit.xml --tb=short',
    'pytest_results': results, 'commands': commands,
    'frozen_originals_preserved': len(preserved), 'frozen_original_paths': preserved,
    'authorized_runtime_changes_vs_governance_baseline': authorized_changes,
    'v3_sha256': sha((ROOT / 'docs/01_需求与范围/开发要求V3.0.md').read_bytes()),
    'tasks': {'total': len(states), 'completed_scoped': len(done), 'pending': len(pending)},
    'current_links_checked': links, 'anchors_checked': anchors,
    'source_hashes': source_hashes, 'artifact_sha256': artifact_hashes,
    'limitations': ['live model and paid budget NOT authorized', 'no Docker on this machine',
                    'B execution-host qualification not received', 'native candidate final NOT independently verified',
                    'two approved providers NOT live verified', 'fixed entry and trace UI NOT implemented',
                    'offline samples are excerpts; their original tool/code paths refer to ignored runs workspaces',
                    'one existing platform skip and dependency deprecation warning retained in JUnit',
                    'earlier governance-only validator and JSON retain their original scope'],
    'errors': errors}
if args.output:
    dest = (ROOT / args.output).resolve()
    if not dest.is_relative_to(ROOT):
        raise ValueError('输出必须在仓库内')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
print(json.dumps({k: result[k] for k in ('pytest_results', 'frozen_originals_preserved', 'tasks',
                                       'current_links_checked', 'anchors_checked', 'errors')}, ensure_ascii=False))
raise SystemExit(bool(errors))
