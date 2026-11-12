"""Apply the approved three-file RPC update; no network or credentials."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

FILES = ('docker/rpc_codec.py', 'docker/rpc_proxy.py', 'docker/candidate_rpc.py')


def apply(package, root, data):
    package, root, data = Path(package).resolve(), Path(root).resolve(), Path(data).resolve()
    manifest = json.loads((package/'rpc-upgrade-manifest.json').read_text(encoding='utf-8'))
    if set(manifest['files']) != set(FILES):
        raise ValueError('upgrade must contain exactly the reviewed three RPC files')
    changes = []
    for name in FILES:
        source, target = package/name, root/name
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError('linked deployment path: ' + name)
        expected = manifest['files'][name]
        raw, old = source.read_bytes(), target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected['after']:
            raise ValueError('upgrade checksum mismatch: ' + name)
        current = hashlib.sha256(old).hexdigest()
        if current not in (expected['before'], expected['after']):
            raise ValueError('server file differs from reviewed baseline: ' + name)
        changes.append((target, raw, old))
    deployment = root/'execution-package-manifest.json'
    if deployment.is_file():
        old = deployment.read_bytes()
        current = json.loads(old)
        for name in FILES:
            current['files'][name] = manifest['files'][name]['after']
        changes.append((deployment, json.dumps(current, ensure_ascii=False, indent=2).encode('utf-8'), old))
    backup = data/('rpc-upgrade-103-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True, exist_ok=False)
    for target, raw, old in changes:
        saved = backup/target.relative_to(root)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(old)
    written = []
    try:
        for target, raw, old in changes:
            temporary = target.with_name(target.name+'.upgrade-103.tmp')
            with temporary.open('xb') as stream:
                stream.write(raw)
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
            written.append((target, old))
        for name in FILES:
            assert hashlib.sha256((root/name).read_bytes()).hexdigest() == manifest['files'][name]['after']
    except BaseException:
        for target, old in reversed(written):
            target.write_bytes(old)
            os.chmod(target, 0o644)
        raise
    print(json.dumps({'passed': True, 'backup': str(backup), 'files': list(FILES),
                      'scope': 'RPC source only; no test/model/access-code changes'}))


if __name__ == '__main__':
    apply(Path(__file__).resolve().parent, '/opt/codefix', '/opt/codefix-data')
