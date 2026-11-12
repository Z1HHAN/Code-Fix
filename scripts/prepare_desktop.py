"""Assemble only application assets and a portable copy of installed Python."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare_backend(destination):
    from scripts.build_execution_package import package_files
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in package_files():
        if name in {'app/cli.py', 'app/model_client.py', 'app/mock_workflow.py',
                    'app/model_context.py', 'app/quixbugs_import.py'}:
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    for name in ('ui/app', 'prompts', 'knowledge/demo_project'):
        shutil.copytree(ROOT / name, destination / name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('decision_rules.json', 'runtime_limits.json'):
        shutil.copyfile(ROOT / 'configs' / name, destination / 'configs' / name)
    runtime = json.loads((ROOT / 'configs/agents.json').read_text(encoding='utf-8'))['runtime_v3']
    runtime['models'] = {'primary': {'binding': 'approved_profile', 'profile_ref': 'personal_model'}}
    for agent in runtime['agents']:
        agent['model_ref'] = 'primary'
    (destination / 'configs/desktop-roles.json').write_text(json.dumps(runtime, ensure_ascii=False), encoding='utf-8')
    # -I suppresses cwd imports; this fixed launcher adds only its own runtime.
    (destination / 'desktop-launch.py').write_text(
        'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parent))\n'
        'from app.desktop_bootstrap import main\nmain()\n', encoding='utf-8')
    (destination / 'desktop-check.py').write_text(
        'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parent))\n'
        'from app.connection_checks import main\nmain()\n', encoding='utf-8')
    manifest = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(destination.rglob('*')) if p.is_file()}
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (destination / 'desktop-build.json').write_text(json.dumps({'source_sha256': fingerprint, 'files': manifest}), encoding='utf-8')
    print('Personal backend files:', len(manifest), 'source:', fingerprint)


def copy_python(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not (source / 'python.exe').is_file() or not (source / 'Lib').is_dir():
        raise ValueError('需要完整Windows Python安装目录，不能使用venv目录')
    if source == destination or destination.is_relative_to(source):
        raise ValueError('Python目标目录不能位于源目录内部')
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', 'test', 'tests', 'Doc', 'Docs', 'tcl', 'Tools', 'Scripts'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', type=Path, default=ROOT / 'desktop/build/backend')
    parser.add_argument('--python-source', type=Path)
    args = parser.parse_args()
    prepare_backend(args.backend)
    if args.python_source:
        copy_python(args.python_source, ROOT / 'desktop/build/python')
