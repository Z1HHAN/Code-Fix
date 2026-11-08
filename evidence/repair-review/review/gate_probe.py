from pathlib import Path
import importlib.util,tempfile,json,sys
sys.path.insert(0,str(Path.cwd()))
spec=importlib.util.spec_from_file_location('offline_acceptance',Path('scripts/c_acceptance.py'));mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory(prefix='gate-probe-') as d:
    root=Path(d);(root/'acceptance_tests').mkdir();(root/'repair_gates').mkdir()
    (root/'acceptance_tests/case_map.json').write_text(json.dumps({'required_testcase_count':25,'required_testcases':['only_one']}))
    (root/'repair_gates/junit.xml').write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="probe" name="only_one"/></testsuite>')
    record={'stage':'repair_gates','status':'passed','exit_code':0};mod.assess(record,root,root)
    Path('runs/review-package-check/gate-probe.json').write_text(json.dumps(record,indent=2))
    print('Declared count 25, manifest and collected 1, assessed:',record['status'])
