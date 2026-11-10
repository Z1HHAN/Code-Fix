"""Verify uploaded source bytes before building or starting the cloud controller."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'execution-package-manifest.json').read_text(encoding='utf-8'))
for name, expected in manifest['files'].items():
    path = root / name
    if (not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise SystemExit('Invalid package path: ' + name)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('Package hash mismatch: ' + name)
print('Verified package files:', len(manifest['files']))
