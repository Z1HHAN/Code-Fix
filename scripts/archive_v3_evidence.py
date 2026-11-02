"""Archive a completed local HTTP baseline bundle and verify every file hash."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('run_id')
parser.add_argument('--base-url', default='http://127.0.0.1:8000')
args = parser.parse_args()
url = urlsplit(args.base_url)
if url.hostname not in {'127.0.0.1', 'localhost'} or url.scheme != 'http' or url.username or url.password:
    raise SystemExit('Only a local Code Fix server is accepted')
import re
if not re.fullmatch(r'run-[a-f0-9]{32}', args.run_id):
    raise SystemExit('Invalid run ID')
with build_opener(ProxyHandler({})).open(args.base_url.rstrip('/') + '/api/v3/tasks/' + args.run_id + '/bundle', timeout=20) as response:
    data = response.read(64*1024*1024+1)
if len(data) > 64*1024*1024:
    raise SystemExit('Bundle too large')
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    manifest = json.loads(archive.read('sha256.json'))
    for name, digest in manifest.items():
        if hashlib.sha256(archive.read(name)).hexdigest() != digest:
            raise SystemExit('Evidence checksum mismatch: ' + name)
    report = json.loads(archive.read('report.json'))
    if report['run']['task']['mode'] != 'baseline' or report['run']['result']['model_api_called'] is not False:
        raise SystemExit('This archive entry is for baseline evidence only')
out = Path(__file__).resolve().parents[1] / 'evidence/v3'
out.mkdir(parents=True, exist_ok=True)
target = out / (args.run_id + '.zip')
if target.exists():
    raise SystemExit('Refusing to overwrite an existing evidence bundle')
target.write_bytes(data)
print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(data).hexdigest(), 'files_verified': len(manifest)}, indent=2))
