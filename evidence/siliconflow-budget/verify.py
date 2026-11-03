"""Read-only review of this batch. No API, candidate or Docker execution.

Use --output with a NEW path to save a report; earlier proof is immutable.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HUB = ROOT / 'docs/2026-11-03_项目持续维护'
parser = argparse.ArgumentParser()
parser.add_argument('--output')
args = parser.parse_args()
output = (ROOT / args.output).resolve() if args.output else None
if output and (not output.is_relative_to(ROOT) or output.exists()):
    raise ValueError('Only a new in-repository report may be written')
sha = lambda raw: hashlib.sha256(raw).hexdigest()
errors = []

def fail(kind, detail):
    errors.append({'kind': kind, 'detail': detail})

baseline = json.loads((HUB/'核验资料/治理前文件指纹.json').read_text(encoding='utf-8'))
previous = json.loads((ROOT/'evidence/contest-core/offline-a/verification.json').read_text(encoding='utf-8'))
mapping = {r['原路径']: r['实际保存路径'] for r in csv.DictReader((HUB/'文件迁移与合并映射.csv').open(encoding='utf-8',newline=''))}
# These four files were frozen only for the prior Stage A batch, and now have
# explicit SiliconFlow authorization. Historical proof/C/V3 bytes have no exception.
allowed = {'app/cli.py', 'configs/agents.json', 'ui/app/app.js', 'ui/app/index.html'}
preserved, changes = [], []
for name in previous['frozen_original_paths']:
    path = ROOT / mapping.get(name, name)
    actual = sha(path.read_bytes()) if path.is_file() else None
    if name in allowed:
        changes.append({'original': name, 'previous_sha256': baseline['hashes'][name], 'current_sha256': actual})
    elif actual != baseline['hashes'][name]:
        fail('frozen_bytes_changed',name)
    else:
        preserved.append(name)

states = {}
for ident, body in re.findall(r'^## (T-\d{3})[^\n]*\n(.*?)(?=^## |\Z)',(HUB/'当前开发任务台账.md').read_text(encoding='utf-8'),re.M|re.S):
    fields = dict(re.findall(r'^\| ([^|]+) \| ([^|]*) \|$', body, re.M))
    states[ident] = {k:fields.get(k) for k in ('实现状态','测试状态','独立验收状态')}
pending = set(re.findall(r'\[(T-\d{3}) ',(HUB/'当前待办索引.md').read_text(encoding='utf-8')))
done = set(re.findall(r'\[(T-\d{3}) ',(HUB/'已完成任务索引.md').read_text(encoding='utf-8')))
if len(states)!=47 or len(done)!=27 or len(pending)!=20 or pending&done or pending|done!=set(states):
    fail('task_partition',{'tasks':len(states),'done':len(done),'pending':len(pending)})
if states.get('T-060') != {'实现状态':'已实现','测试状态':'通过','独立验收状态':'未运行'}:
    fail('scope_status',states.get('T-060'))
if states.get('T-033') != {'实现状态':'开发中','测试状态':'待确认','独立验收状态':'未运行'}:
    fail('remaining_model_pricing_not_fully_verified',states.get('T-033'))

links, anchors = 0,0
for doc in list(HUB.rglob('*.md'))+[ROOT/p for p in ('README.md','CHANGELOG.md','AGENTS.md')]:
    contents = re.sub(r'```.*?```','',doc.read_text(encoding='utf-8-sig'),flags=re.S)
    for match in re.finditer(r'!?\[[^\]\n]*\]\((<[^>\n]+>|[^)\s\n]+)\)',contents):
        url = match[1].strip('<>')
        if re.match(r'^(?:[a-zA-Z][\w+.-]*:|/)',url): continue
        name,_,anchor = url.partition('#')
        target = (doc.parent/unquote(name)).resolve() if name else doc
        links+=1
        if not target.is_relative_to(ROOT) or (not target.exists() and target!=output):
            fail('link',{'doc':str(doc.relative_to(ROOT)),'url':url})
        elif anchor and target.suffix.lower()=='.md':
            anchors+=1
            text=target.read_text(encoding='utf-8-sig')
            ids=set(re.findall(r'<a\s+id="([^"]+)"',text))
            ids|={re.sub(r'[^\w\s\-\u4e00-\u9fff]','',h.lower()).replace(' ','-') for h in re.findall(r'^#{1,6} (.+)$',text,re.M)}
            if unquote(anchor) not in ids: fail('anchor',url)

results={}
for path in HERE.glob('*.junit.xml'):
    counts={k:0 for k in ('tests','failures','errors','skipped')}
    for suite in ET.parse(path).getroot().iter('testsuite'):
        for k in counts: counts[k]+=int(suite.get(k,0))
    counts['passed']=counts['tests']-counts['failures']-counts['errors']-counts['skipped']
    results[path.name]=counts
final=results.get('final-platform.junit.xml',{})
if final.get('failures')!=0 or final.get('errors')!=0 or final.get('passed',0)<240:
    fail('final_regression',final)

sources=[]
for folder in ('app','tests','configs','prompts','ui','scripts','c_runner','c_tests'):
    sources += [p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.json','.md','.js','.cjs','.html','.css','.ps1'}]
sources += [ROOT/p for p in ('requirements-lock.txt','requirements.txt','pytest.ini','.gitattributes')]
hashes = {p.relative_to(ROOT).as_posix():{'raw_sha256':sha(p.read_bytes()),'lf_normalized_sha256':sha(p.read_bytes().replace(b'\r\n',b'\n'))} for p in sorted(sources)}
artifacts={p.relative_to(HERE).as_posix():sha(p.read_bytes()) for p in sorted(HERE.rglob('*')) if p.is_file() and p!=output}
result={'scope':'Offline regression plus separately labelled live metadata/JSON smoke; no live repair, Docker or public/candidate execution',
        'development_base_commit':'40fca6cc98ef06659925dd25421fbd4463a90006',
        'git_head_at_check':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'pytest_results':results,'source_hashes':hashes,'artifact_sha256':artifacts,
        'frozen_originals_preserved':len(preserved),'authorized_changes_vs_previous_batch':changes,
        'v3_sha256':sha((ROOT/'docs/01_需求与范围/开发要求V3.0.md').read_bytes()),
        'tasks':{'total':len(states),'done_scoped':len(done),'pending':len(pending)},
        'current_links_checked':links,'anchors_checked':anchors,'errors':errors,
        'limitations':['Project estimates are not live platform balances','Two accounts are ONE provider',
                      'B/C independent rerun pending','Other model pricing/billable bounds pending',
                      'Single execution-machine ledger; separate databases do not share a team cap',
                      'No stable contest acceptance or real repair implied by smoke checks']}
if output:
    with output.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in result.items() if k not in ('source_hashes','artifact_sha256')},ensure_ascii=False,indent=2))
raise SystemExit(int(bool(errors)))
