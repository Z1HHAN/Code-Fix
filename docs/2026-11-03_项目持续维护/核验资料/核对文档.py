"""文档治理只读核验；仅更新核验JSON，不执行产品代码、模型或目标测试。"""
from pathlib import Path
from urllib.parse import unquote
import csv
import hashlib
import json
import re
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HUB = HERE.parent
BASE = json.loads((HERE / '治理前文件指纹.json').read_text(encoding='utf-8'))
SHA = lambda b: hashlib.sha256(b).hexdigest()
mapping_rows = list(csv.DictReader((HUB / '文件迁移与合并映射.csv').open(encoding='utf-8', newline='')))
mapping = {r['原路径']: r['实际保存路径'] for r in mapping_rows}
errors = []
preserved = []

def fail(kind, detail):
    errors.append({'kind': kind, 'detail': detail})

for original, digest in BASE['hashes'].items():
    target = ROOT / mapping.get(original, original)
    if not target.is_file():
        fail('missing_original', original)
        continue
    # Product code/config/assets/raw evidence and historical audit source files are frozen.
    immutable = not original.endswith('.md') or original == 'docs/01_需求与范围/开发要求V3.0.md'
    immutable |= original.startswith(('evidence/', 'docs/99_历史归档/', 'docs/integration/')) and original not in {
        'docs/99_历史归档/历史归档索引.md', 'docs/99_历史归档/固定位置历史材料索引.md'
    }
    if immutable:
        if SHA(target.read_bytes()) != digest:
            fail('immutable_changed', original)
        else:
            preserved.append(original)

with zipfile.ZipFile(ROOT / 'docs/99_历史归档/文档治理审计/持续维护切换/原文快照.zip') as snapshot:
    for original, digest in BASE['hashes'].items():
        if original.endswith('.md') and SHA(snapshot.read(original)) != digest:
            fail('snapshot_mismatch', original)
    original_ledger = snapshot.read('docs/04_开发与任务管理/当前开发任务台账.md').decode('utf-8-sig')

def states(text):
    text = text.replace('\r\n', '\n')
    out = {}
    for ident, body in re.findall(r'^## (T-\d{3})[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S):
        fields = dict(re.findall(r'^\| ([^|]+) \| ([^|]*) \|$', body, re.M))
        out[ident] = {k: fields.get(k) for k in ('实现状态', '测试状态', '独立验收状态')}
        if any(v is None for v in out[ident].values()):
            fail('missing_status_field', ident)
    return out

old_states = states(original_ledger)
new_states = states((HUB / '当前开发任务台账.md').read_text(encoding='utf-8'))
# 2026-11-03老师指导后仅登记两个静态缺口；不放宽原43项状态核对。
added_ids = set(new_states) - set(old_states)
original_states_unchanged = old_states == {k: new_states.get(k) for k in old_states}
if not original_states_unchanged or added_ids != {'T-057', 'T-058'} or len(new_states) != 45:
    fail('task_states_changed', {'before': old_states, 'after': new_states})
for ident in added_ids:
    if new_states[ident] != {'实现状态': '未开始', '测试状态': '未运行', '独立验收状态': '未运行'}:
        fail('unverified_new_task_promoted', ident)
ledger = (HUB / '当前开发任务台账.md').read_text(encoding='utf-8')
for name in ('当前待办索引.md', '已完成任务索引.md'):
    ids = re.findall(r'\[T-(\d{3}) ', (HUB / name).read_text(encoding='utf-8'))
    if len(ids) != len(set(ids)) or any('T-' + i not in new_states for i in ids):
        fail('task_index_mismatch', name)
pending = set(re.findall(r'\[T-(\d{3}) ', (HUB / '当前待办索引.md').read_text(encoding='utf-8')))
done = set(re.findall(r'\[T-(\d{3}) ', (HUB / '已完成任务索引.md').read_text(encoding='utf-8')))
if len(pending) != 25 or len(done) != 20 or pending & done or len(pending | done) != 45:
    fail('task_partition', {'pending': sorted(pending), 'done': sorted(done)})

link_re = re.compile(r'!?\[[^\]\n]*\]\((<[^>\n]+>|[^)\s\n]+)\)')
current = list(HUB.rglob('*.md'))
current += [ROOT / p for p in ('README.md', 'AGENTS.md', 'CHANGELOG.md', '.github/pull_request_template.md',
    'c_runner/README.md', 'c_tests/README.md', 'c_fixtures/README.md', 'task_assets/leap_legacy/README.md',
    'task_assets/leap_legacy/队长今日执行与台账.md', 'ui/prototype/README.md',
    'docs/99_历史归档/历史归档索引.md', 'docs/99_历史归档/固定位置历史材料索引.md')]
checked = 0
anchors_checked = 0
for doc in current:
    content = doc.read_text(encoding='utf-8-sig')
    # Code examples are templates, not actual links.
    content = re.sub(r'```.*?```', '', content, flags=re.S)
    for m in link_re.finditer(content):
        url = m[1].strip('<>')
        if re.match(r'^(?:[a-zA-Z][\w+.-]*:|/)', url):
            continue
        path, _, anchor = url.partition('#')
        target = (doc.parent / unquote(path)).resolve() if path else doc
        checked += 1
        if not target.is_relative_to(ROOT) or not target.exists():
            fail('broken_current_link', {'file': doc.relative_to(ROOT).as_posix(), 'url': url})
        elif anchor and target.suffix.lower() == '.md':
            target_text = target.read_text(encoding='utf-8-sig')
            explicit = set(re.findall(r'<a\s+id="([^"]+)"', target_text))
            headings = re.findall(r'^#{1,6} (.+)$', target_text, re.M)
            heading_ids = {re.sub(r'[^\w\s\-\u4e00-\u9fff]', '', h.lower()).replace(' ', '-') for h in headings}
            anchors_checked += 1
            if unquote(anchor) not in explicit | heading_ids:
                fail('missing_anchor', {'file': doc.relative_to(ROOT).as_posix(), 'url': url})

for old, new in mapping.items():
    if (ROOT / old).is_file():
        fail('old_entry_still_exists', old)
    if not (ROOT / new).is_file():
        fail('missing_target', new)
for row in csv.DictReader((HUB / '历史引用追溯表.csv').open(encoding='utf-8', newline='')):
    if not (ROOT / row['当前可读路径']).exists():
        fail('historical_resolution_missing', row)
for dir_name in ('docs/daily', 'docs/deliveries'):
    path = ROOT / dir_name
    if path.exists() and any(path.rglob('*')):
        fail('daily_docs_still_active', dir_name)

# No executable reference moved: check literal paths in runtime config and knowledge metadata.
plan = json.loads((ROOT / 'configs/test_plan.json').read_text(encoding='utf-8-sig'))
def find_reports(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'report' and isinstance(v, str) and v.startswith('docs/') and not (ROOT / v).is_file():
                fail('runtime_report_path_missing', v)
            find_reports(v)
    elif isinstance(obj, list):
        for v in obj:
            find_reports(v)
find_reports(plan)
for p in (ROOT / 'knowledge').rglob('*.md'):
    for v in re.findall(r'^source_path: (.+)$', p.read_text(encoding='utf-8'), re.M):
        if not (ROOT / v.strip()).exists():
            fail('knowledge_source_missing', {'file': p.relative_to(ROOT).as_posix(), 'path': v})

config = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'app.cli', 'validate-config'],
                        cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
if config.returncode != 0:
    fail('config_check_failed', config.stdout + config.stderr)
else:
    try:
        if json.loads(config.stdout).get('errors') != []:
            fail('config_errors_present', config.stdout)
    except json.JSONDecodeError:
        fail('config_output_not_json', config.stdout)

# Every still-existing historical MD may contain source-version relative paths; do not rewrite evidence.
historical_links = []
for doc in (ROOT / 'docs/99_历史归档').rglob('*.md'):
    if 'C成员首日交付_2026-10-30' in doc.parts:
        continue  # frozen C package: validate its bytes, never reinterpret internal evidence
    for m in link_re.finditer(doc.read_text(encoding='utf-8-sig')):
        url = m[1].strip('<>')
        if re.match(r'^(?:[a-zA-Z][\w+.-]*:|#|/)', url):
            continue
        target = (doc.parent / unquote(url.split('#', 1)[0])).resolve()
        if not target.exists():
            historical_links.append({'file': doc.relative_to(ROOT).as_posix(), 'source_version_link': url,
                'handling': '保留原始历史字节；按原文快照及迁移映射追溯，不作为当前操作入口'})

# Track originals by manifest rather than relying on an already-clean working tree after publication.
result = {
    'checked_date': '2026-11-03', 'baseline_commit': BASE['base'],
    'scope': '文档/路径/状态治理与老师指导登记，仅配置静态自检；未新跑pytest、Node、模型、Docker或公开题',
    'tracked_original_files': len(BASE['hashes']), 'mapped_files': len(mapping),
    'preserved_immutable_files': len(preserved), 'original_markdown_snapshots': len([p for p in BASE['hashes'] if p.endswith('.md')]),
    'v3_sha256': SHA((ROOT / 'docs/01_需求与范围/开发要求V3.0.md').read_bytes()),
    'task_ids': len(new_states), 'original_task_states_unchanged': original_states_unchanged,
    'registered_new_task_ids': sorted(added_ids),
    'pending_index': len(pending), 'completed_scoped_index': len(done),
    'current_markdown_files': len(current), 'current_relative_links_checked': checked, 'anchors_checked': anchors_checked,
    'config_check': {'command': '<运行核验脚本的同一Python> -X utf8 -m app.cli validate-config',
                     'exit_code': config.returncode, 'stdout': config.stdout, 'stderr': config.stderr},
    'frozen_historical_link_limitations': historical_links,
    'note': '历史MD保留原文和原相对链接；不可将其当当前入口，需通过当前索引/映射/Git查询原版本。当前文档链接单独全检。',
    'errors': errors,
}
(HUB / '整理核验结果.json').write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
print(json.dumps({k: v for k, v in result.items() if k not in ('frozen_historical_link_limitations',)}, ensure_ascii=False, indent=2))
print('frozen_historical_link_limitations:', len(historical_links))
raise SystemExit(bool(errors))
