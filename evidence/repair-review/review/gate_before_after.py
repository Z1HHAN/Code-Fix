from pathlib import Path
import subprocess,types,importlib.util,tempfile,json,sys
sys.path.insert(0,str(Path.cwd()))
import pytest
path=Path('acceptance_tests/test_repair_gates.py')
spec=importlib.util.spec_from_file_location('review_gates',path)
gates=importlib.util.module_from_spec(spec);spec.loader.exec_module(gates)
old=types.ModuleType('app.review_old_runner');old.__package__='app';old.__file__=str(Path('app/docker_runner.py').resolve())
exec(compile(subprocess.check_output(['git','show','9d5b5be:app/docker_runner.py']),str(Path('app/docker_runner.py').resolve()),'exec'),old.__dict__)
results={}
current=gates.DockerRunner
for name,runner in [('original_product',old.DockerRunner),('fixed_product',current)]:
    gates.DockerRunner=runner
    with tempfile.TemporaryDirectory(prefix='code07-') as directory:
        patch=pytest.MonkeyPatch()
        try:
            gates.test_code07_execution_version_bound_to_snapshot_during_mutation(Path(directory),patch)
            results[name]={'status':'passed'}
        except BaseException as exc:
            results[name]={'status':'failed','type':type(exc).__name__,'reason':str(exc)}
        finally:patch.undo()
assert results['original_product']['status']=='failed',results
assert results['fixed_product']['status']=='passed',results
Path('runs/review-package-check/code07-before-after.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(results,ensure_ascii=False))
