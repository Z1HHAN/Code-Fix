"""Build a cloud upload package without Git, team configuration, or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package_files(root=ROOT):
    from app.tasks_v3 import catalog
    files = set()
    for name in ('app', 'c_runner'):
        files.update(p.relative_to(root).as_posix() for p in (root / name).rglob('*.py'))
    for name in ('c_runner/profiles.json', 'configs/tasks/catalog-v3.json', 'requirements-lock.txt',
                 'docker/runner.Dockerfile', 'docker/execution-service.Dockerfile', 'docker/.dockerignore',
                 'scripts/prepare_execution_cloud.sh', 'scripts/start_execution_cloud.sh',
                 'scripts/verify_execution_package.py', 'deploy/cloud/README.md'):
        files.add(name)
    files.update(p.relative_to(root).as_posix() for p in (root / 'docker').glob('*.py'))
    for task in catalog().values():
        profile = task['profile']
        for name, expected in profile['approved_files'].items():
            path = root / profile['fixture_dir'] / name
            if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
                raise ValueError('Package input contains links')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Registered fixture hash mismatch: ' + name)
            files.add(path.relative_to(root).as_posix())
    return sorted(files)


def build(destination):
    files = package_files()
    fingerprints = {}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwriting an earlier upload artifact.
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            raw = (ROOT / name).read_bytes()
            if name.endswith('.sh'):
                raw = raw.replace(b'\r\n', b'\n')
            fingerprints[name] = hashlib.sha256(raw).hexdigest()
            archive.writestr(name, raw)
        archive.writestr('execution-package-manifest.json', json.dumps({
            'schema_version': 1, 'scope': 'cloud isolation service; no model configuration or model credentials',
            'files': fingerprints}, ensure_ascii=False, indent=2))
    print(json.dumps({'path': str(destination.resolve()), 'files': len(files),
                      'bytes': destination.stat().st_size,
                      'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    build(parser.parse_args().output)
