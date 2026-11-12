"""Cloud update preflight and rollback against disposable synthetic files."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def setup_upgrade(tmp_path):
    spec = importlib.util.spec_from_file_location('rpc_upgrade', Path('deploy/cloud/update_rpc_103.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package, root, data = (tmp_path/name for name in ('package', 'server', 'data'))
    files = {}
    for name in module.FILES:
        for directory, content in ((package, b'new source'), (root, b'old source')):
            path = directory/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        files[name] = {'before': hashlib.sha256(b'old source').hexdigest(),
                       'after': hashlib.sha256(b'new source').hexdigest()}
    (package/'rpc-upgrade-manifest.json').write_text(json.dumps({'files': files}))
    return module, package, root, data


def test_update_uses_reviewed_files_and_preserves_backup(tmp_path):
    module, package, root, data = setup_upgrade(tmp_path)
    module.apply(package, root, data)
    for name in module.FILES:
        assert (root/name).read_bytes() == b'new source'
        assert next(data.glob('rpc-upgrade-103-*/'+name)).read_bytes() == b'old source'


def test_different_server_version_is_rejected_before_any_write(tmp_path):
    module, package, root, data = setup_upgrade(tmp_path)
    (root/module.FILES[-1]).write_bytes(b'other version')
    with pytest.raises(ValueError, match='baseline'):
        module.apply(package, root, data)
    assert (root/module.FILES[0]).read_bytes() == b'old source'
    assert not data.exists()


def test_failed_replacement_restores_files_already_changed(tmp_path, monkeypatch):
    module, package, root, data = setup_upgrade(tmp_path)
    replace = module.os.replace
    calls = []
    def fail_second(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError('simulated write failure')
        return replace(source, destination)
    monkeypatch.setattr(module.os, 'replace', fail_second)
    with pytest.raises(OSError, match='simulated'):
        module.apply(package, root, data)
    assert all((root/name).read_bytes() == b'old source' for name in module.FILES)
