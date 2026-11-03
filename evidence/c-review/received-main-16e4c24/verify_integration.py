"""Read-only merge/classification checks; never runs models or candidate code."""
from pathlib import Path
import argparse, csv, hashlib, json, re, subprocess, zipfile
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
HUB=ROOT/'docs/2026-11-03_项目持续维护'
parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
args=parser.parse_args()
output=(ROOT/args.output).resolve()
assert output.is_relative_to(ROOT) and not output.exists()
sha=lambda raw:hashlib.sha256(raw).hexdigest()
errors=[]
def check(ok,kind,detail=None):
    if not ok:errors.append({'kind':kind,'detail':detail})
received=json.loads((HERE/'接收与校验.json').read_bytes())
archived=0
for p in (ROOT/received['archive']).iterdir():
    raw=subprocess.check_output(['git','show','16e4c24:C_20261103交付/'+p.name],cwd=ROOT)
    check(p.read_bytes()==raw,'original_git_bytes',p.name);archived+=1
check(archived==7,'original_count',archived)
check(not (ROOT/'C_20261103交付').exists(),'old_root_removed')
for copy in received['copies']:
    check(sha((ROOT/copy['target']).read_bytes())==copy['sha256'],'copy_bytes',copy['target'])
inventory=list(csv.DictReader((HERE/'文件处置映射.csv').open(encoding='utf-8',newline='')))
check(len(inventory)==8713,'inventory_count',len(inventory))
for package in received['package_results']:
    p=ROOT/package['archive'];check(sha(p.read_bytes())==package['sha256'],'zip_bytes',p.name)
    with zipfile.ZipFile(p) as z:
        check(z.testzip() is None,'zip_crc',p.name)
        manifest=json.loads(z.read('SHA256.json'))
        for name,digest in manifest.items():check(sha(z.read(name))==digest,'zip_internal_hash',name)
original=json.loads((ROOT/'evidence/siliconflow-budget/verification.json').read_bytes())
preserved_sources=0
for name,hashes in original['source_hashes'].items():
    if name=='.gitattributes':continue
    check(sha((ROOT/name).read_bytes().replace(b'\r\n',b'\n'))==hashes['lf_normalized_sha256'],'existing_source_unchanged',name)
    preserved_sources+=1
frozen=json.loads((ROOT/'evidence/contest-core/offline-a/verification.json').read_bytes())
baseline=json.loads((HUB/'核验资料/治理前文件指纹.json').read_bytes())
mapping={r['原路径']:r['实际保存路径'] for r in csv.DictReader((HUB/'文件迁移与合并映射.csv').open(encoding='utf-8',newline=''))}
protected=0
for name in frozen['frozen_original_paths']:
    if name in {'app/cli.py','configs/agents.json','ui/app/app.js','ui/app/index.html'}:continue
    p=ROOT/mapping.get(name,name)
    check(p.is_file() and sha(p.read_bytes())==baseline['hashes'][name],'protected_original',name);protected+=1
v3hash=sha((ROOT/'docs/01_需求与范围/开发要求V3.0.md').read_bytes())
check(v3hash==original['v3_sha256'],'v3_unchanged')
ledger=(HUB/'当前开发任务台账.md').read_text(encoding='utf-8')
ids=set(re.findall(r'^## (T-\d{3}) ',ledger,re.M))
done=set(re.findall(r'\[(T-\d{3}) ',(HUB/'已完成任务索引.md').read_text(encoding='utf-8')))
pending=set(re.findall(r'\[(T-\d{3}) ',(HUB/'当前待办索引.md').read_text(encoding='utf-8')))
check(len(ids)==48 and len(done)==27 and len(pending)==21 and not done&pending and done|pending==ids,'task_partition')
links=anchors=0
for doc in list(HUB.rglob('*.md'))+[ROOT/p for p in ('README.md','CHANGELOG.md','AGENTS.md','docs/99_历史归档/历史归档索引.md')]:
    contents=re.sub(r'```.*?```','',doc.read_text(encoding='utf-8-sig'),flags=re.S)
    for match in re.finditer(r'!?\[[^\]\n]*\]\((<[^>\n]+>|[^)\s\n]+)\)',contents):
        url=match[1].strip('<>')
        if re.match(r'^(?:[a-zA-Z][\w+.-]*:|/)',url):continue
        name,_,anchor=url.partition('#')
        target=(doc.parent/unquote(name)).resolve() if name else doc
        links+=1
        check(target.is_relative_to(ROOT) and (target.exists() or target==output),'current_link',{'doc':str(doc.relative_to(ROOT)),'url':url})
        if anchor and target.is_file() and target.suffix.lower()=='.md':
            anchors+=1;text=target.read_text(encoding='utf-8-sig')
            found=set(re.findall(r'<a\s+id="([^"]+)"',text))
            found|={re.sub(r'[^\w\s\-\u4e00-\u9fff]','',h.lower()).replace(' ','-') for h in re.findall(r'^#{1,6} (.+)$',text,re.M)}
            check(unquote(anchor) in found,'current_anchor',url)
def counts(path):
    result={k:0 for k in ('tests','failures','errors','skipped')}
    for suite in ET.parse(path).getroot().iter('testsuite'):
        for key in result:result[key]+=int(suite.get(key,0))
    result['passed']=result['tests']-result['failures']-result['errors']-result['skipped'];return result
platform=counts(ROOT/'evidence/c-review/integration-check/entry-platform_pytest-junit.xml')
gates=counts(ROOT/'evidence/c-review/integration-check/repair-gates.junit.xml')
check(platform=={'tests':241,'failures':0,'errors':0,'skipped':1,'passed':240},'platform_result',platform)
check(gates=={'tests':25,'failures':23,'errors':0,'skipped':0,'passed':2},'gates_result',gates)
required=set(json.loads((ROOT/'acceptance_tests/case_map.json').read_bytes())['required_testcases'])
cases=[c.get('name') for c in ET.parse(ROOT/'evidence/c-review/integration-check/repair-gates.junit.xml').getroot().iter('testcase')]
check(len(cases)==25 and set(cases)==required,'required_case_manifest')
partial=json.loads((ROOT/'evidence/c-review/integration-check/入口失败观察.json').read_bytes())
rawzip=ROOT/partial['raw_archive'];check(sha(rawzip.read_bytes())==partial['raw_archive_sha256'],'partial_zip_bytes')
with zipfile.ZipFile(rawzip) as z:
    for entry in partial['raw_archive_files']:check(sha(z.read(entry['name']))==entry['sha256'],'partial_raw_artifact',entry['name'])
node=(ROOT/'evidence/c-review/integration-check/node-ui.stdout.txt').read_text(encoding='utf-8')
check(bool(re.search(r'(?:ℹ|#) pass 12\b',node)) and bool(re.search(r'(?:ℹ|#) fail 0\b',node)),'node_result')
result={'scope':'classification and offline merge review; failed new gates retained; not stable or true repair acceptance','git_head_at_check':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'original_files_preserved':archived,'inventory_rows':len(inventory),'zip_internal_hash_entries_verified':8704,'copies_unchanged':len(received['copies']),'existing_sources_unchanged_lf':preserved_sources,'protected_files_unchanged':protected,'v3_sha256':v3hash,'tasks':{'total':len(ids),'historical_scoped_done':len(done),'pending':len(pending)},'current_links_checked':links,'anchors_checked':anchors,'platform':platform,'new_repair_gates':gates,'node_passed':12,'c_entry_complete':False,'stable_acceptance':False,'new_paid_requests':0,'errors':errors}
with output.open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(int(bool(errors)))
