"""Bounded-memory evidence ZIP; limits apply to bytes actually read."""
import hashlib
import json
from tempfile import TemporaryFile
import zipfile

from .run_store import StateConflict

LIMIT = 64 * 1024 * 1024
CHUNK = 64 * 1024


def build_bundle(folder, report):
    buffer = TemporaryFile('w+b')
    try:
        manifest = {}
        encoded = json.dumps(report, ensure_ascii=False, indent=2).encode()
        total = len(encoded)
        if total > LIMIT:
            raise StateConflict('证据包超过64MiB，请由操作员分包归档')
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('report.json', encoded)
            manifest['report.json'] = hashlib.sha256(encoded).hexdigest()
            for path in sorted(folder.rglob('*')):
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise StateConflict('拒绝导出含链接的运行目录')
                if not path.is_file() or path.name == 'report.json':
                    continue
                if total + path.stat().st_size > LIMIT:
                    raise StateConflict('证据包超过64MiB，请由操作员分包归档')
                name = path.relative_to(folder).as_posix()
                if name == 'sha256.json':
                    raise StateConflict('运行目录与导出校验清单重名')
                digest = hashlib.sha256()
                with path.open('rb') as source, archive.open(name, 'w') as dest:
                    while data := source.read(CHUNK):
                        total += len(data)
                        if total > LIMIT:
                            raise StateConflict('证据包超过64MiB，请由操作员分包归档')
                        digest.update(data)
                        dest.write(data)
                manifest[name] = digest.hexdigest()
            archive.writestr('sha256.json', json.dumps(manifest, indent=2))
        buffer.seek(0)
        return buffer
    except BaseException:
        buffer.close()
        raise


def chunks(buffer):
    try:
        while data := buffer.read(CHUNK):
            yield data
    finally:
        buffer.close()
