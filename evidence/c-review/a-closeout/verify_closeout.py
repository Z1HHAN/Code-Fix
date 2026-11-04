from pathlib import Path
import hashlib,json,re,shutil,subprocess,zipfile
root=next(p for p in Path(__file__).resolve().parents if (p/'app').is_dir() and (p/'configs').is_dir())
out=root/'evidence/c-review/a-closeout'
run=root/'evidence/c-review/C-acceptance-20261104-104442-67d70e5d'
sha=lambda b:hashlib.sha256(b).hexdigest()
report=json.loads((run/'summary.json').read_bytes())
assert report['exit_code']==0 and report['source_unchanged_during_checks']
before=json.loads((run/'source-before.json').read_bytes())
assert before==json.loads((run/'source-after.json').read_bytes())
for name,digest in before.items():assert sha((root/name).read_bytes())==digest,name
manifest=json.loads((run/'sha256.json').read_bytes())['sha256']
for name,digest in manifest.items():assert sha((run/name).read_bytes())==digest,name
for step in report['steps']:
    assert step['status']=='passed'
    record=step.get('work_preservation',{})
    if record.get('format')=='zip':
        archive=run/step['stage']/record['archive']
        assert sha(archive.read_bytes())==record['archive_sha256']
        with zipfile.ZipFile(archive) as z:
            for name,digest in record['file_sha256'].items():assert sha(z.read(name))==digest
changed=subprocess.check_output(['git','diff','--name-only','46bf412','--','evidence'],cwd=root).decode().strip()
assert not changed,changed
protected=subprocess.check_output(['git','ls-files','-z','docs/99_历史归档/2026-11/原始交付','docs/01_需求与范围/开发要求V3.0.md','acceptance_tests'],cwd=root).decode().strip('\0').split('\0')
for name in protected:
    blob=subprocess.check_output(['git','show','46bf412:'+name],cwd=root)
    raw=(root/name).read_bytes()
    assert raw==blob or raw.replace(b'\r\n',b'\n')==blob.replace(b'\r\n',b'\n'),name
with zipfile.ZipFile(out/'链接修正前原文.zip') as z:
    backup={name:sha(z.read(name)) for name in z.namelist()}
for name in ['closeout-targeted.junit.xml','closeout-platform.junit.xml','closeout-final-targeted.junit.xml','closeout-api-final.junit.xml','a-closeout-links.json','a-closeout-anchors.json']:
    shutil.copyfile(root/'runs'/name,out/name)
shutil.copyfile(__file__,out/'verify_closeout.py')
links=json.loads((out/'a-closeout-links.json').read_bytes());anchors=json.loads((out/'a-closeout-anchors.json').read_bytes())
assert not links['broken'] and not anchors['broken']
paths=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root).decode().strip('\0').split('\0')
for name in paths:
    p=root/name
    if p.suffix in {'.py','.json','.md','.js','.txt','.yaml','.yml','.xml'}:
        assert not re.search(rb'sk-[a-z]{30,}',p.read_bytes()),'possible credential: '+name
result={'base_commit':'46bf4127f290a92dd752ca4e9020c2c39f667913','tested_worktree_source_sha256':before,
        'final_run':str(run.relative_to(root)),'source_matches_final_run':True,
        'stages':[{k:s.get(k) for k in ('stage','status','counts','scenario_count')} for s in report['steps']],
        'original_C_and_V3_and_gates_unchanged':True,'prior_evidence_unchanged':True,
        'all_final_evidence_and_zip_digests_match':True,'link_backup_sha256':backup,
        'local_links':links['checked'],'broken_local_links':0,'anchors':anchors['anchors'],'broken_anchors':0,
        'new_paid_requests':0,'docker_real_validation':'pending_B','independent_member_signoff':'pending_C',
        'stable_release':False,'scope':'A authorized first five; sixth deliberately pending'}
with (out/'verification.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
print(json.dumps({'sources':len(before),'protected_files':len(protected),'backup_docs':len(backup),'links':links['checked'],'anchors':anchors['anchors'],'errors':[]},ensure_ascii=False))
