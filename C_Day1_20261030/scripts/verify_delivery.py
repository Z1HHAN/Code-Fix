"""Validate local evidence, links, and frozen delivery hashes; no test execution."""
from pathlib import Path
import hashlib
import json
import re
import sys
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CHECKSUM = ROOT / 'handoff/文件校验清单.json'
INDEX = ROOT / 'handoff/文件索引.md'

def files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and not any(x in p.parts for x in ('.venv', '.runtime', '__pycache__', '.git')))

def check(condition, message):
    if not condition:
        raise SystemExit(message)

def main():
    manifests = list((ROOT / 'runs').glob('*/manifest.json'))
    ids = re.findall(r'C-\d{8}-\d{6}-[a-f0-9]{8}', (ROOT / 'README.md').read_text(encoding='utf-8'))
    check(ids and len(set(ids)) == 1, 'README run IDs disagree')
    run_id = ids[0]
    manifest_path = ROOT / 'runs' / run_id / 'manifest.json'
    check(manifest_path in manifests, 'Selected run is missing')
    m = json.loads(manifest_path.read_text(encoding='utf-8'))
    check(m['python_version'].startswith('3.14.'), 'Python evidence is not 3.14')
    check(m['result'] == 'passed' and all(m['validations'].values()), 'Evidence validation failed')
    for kind, expected in [('bad', (3, 1, 1)), ('reference', (4, 0, 0))]:
        record = json.loads((manifest_path.parent / (kind + '.toolrun.json')).read_text(encoding='utf-8'))
        check(record == m['runs'][kind]['tool_run'], 'ToolRun differs from manifest')
        check((record['counts']['passed'], record['counts']['failed'], record['exit_code']) == expected, 'Unexpected test result')
        check((ROOT / record['log_path']).is_file(), 'Missing ToolRun log')
        cases = ET.parse(manifest_path.parent / (kind + '.junit.xml')).findall('.//testcase')
        check(len(cases) == 4 and not any(c.find('skipped') is not None or c.find('error') is not None for c in cases), 'Incomplete JUnit execution')
        check(sum(c.find('failure') is not None for c in cases) == expected[1], 'JUnit failure counts disagree')
    links = 0
    for p in files():
        if p.suffix not in ('.md', '.html') or 'runs' in p.relative_to(ROOT).parts:
            continue
        content = p.read_text(encoding='utf-8')
        targets = re.findall(r'\]\(([^)]+)\)', content) if p.suffix == '.md' else re.findall(r'href=[\"\x27]([^\"\x27]+)', content)
        for target in targets:
            if '://' in target or target.startswith('#'):
                continue
            local = (p.parent / unquote(target.split('#')[0])).resolve()
            check(local.is_relative_to(ROOT) and local.is_file(), f'Broken link: {p.name}: {target}')
            links += 1
    for p in [ROOT / 'README.md', ROOT / 'handoff/给B的消息.txt', *list((ROOT / 'handoff').glob('*.html'))]:
        text = p.read_text(encoding='utf-8')
        check('3.11' not in text, f'Old version: {p.name}')
        check(all(x == run_id for x in re.findall(r'C-\d{8}-\d{6}-[a-f0-9]{8}', text)), f'Wrong run: {p.name}')
    if '--freeze' in sys.argv:
        INDEX.write_text('# 交付文件索引\n\n' + '\n'.join('- [' + p.relative_to(ROOT).as_posix() + '](../' + p.relative_to(ROOT).as_posix() + ')' for p in files() if p not in (INDEX, CHECKSUM)) + '\n', encoding='utf-8')
        data = {'run_id': run_id, 'python_target': '3.14', 'model_api_called': False, 'excluded': ['handoff/文件校验清单.json'], 'sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files() if p != CHECKSUM}}
        CHECKSUM.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    data = json.loads(CHECKSUM.read_text(encoding='utf-8'))
    actual = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files() if p != CHECKSUM}
    check(actual == data['sha256'], 'File set or SHA256 mismatch; freeze only after reviewing all changes')
    print(f'PASS: {len(actual)} files, {links} local links, run {run_id}, Python 3.14 evidence')

if __name__ == '__main__':
    main()
