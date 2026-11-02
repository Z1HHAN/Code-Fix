"""Probe a local live server: real frozen baseline, pause, report and ZIP hashes.

No model requests, candidate execution, fake pytest results or automatic retries.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', default='runs/v3-control-http')
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'}
            or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}):
        raise SystemExit('Only a local Code Fix server root URL is accepted')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    opener = build_opener(ProxyHandler({}))
    base = args.base_url.rstrip('/') + '/api/v3'
    token = ''

    def request(path, body=None):
        headers = {'X-Codefix-Token': token, 'Content-Type': 'application/json'}
        data = None if body is None else json.dumps(body).encode()
        with opener.open(Request(base + path, data=data, headers=headers), timeout=20) as response:
            content = response.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise ValueError('Response too large')
        return content

    token = json.loads(request('/session'))['token']
    rid = json.loads(request('/tasks', {'mode': 'baseline', 'description': '真实HTTP暂停与证据保留验收'}))['run_id']
    json.loads(request(f'/tasks/{rid}/start', {}))
    time.sleep(.1)  # Allow the trusted baseline to enter its current test step.
    control = json.loads(request(f'/tasks/{rid}/control', {'action': 'pause'}))
    assert control['control'] == 'pause'
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        report = json.loads(request(f'/tasks/{rid}/report'))
        if report['run']['status'] != 'running':
            break
        time.sleep(.05)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert report['run']['status'] == 'paused'
    result = report['run']['result']
    assert result['tool_result']['counts']['passed'] == 3
    assert result['tool_result']['counts']['failed'] == 1
    assert result['final_result'] == 'not_run' and result['model_api_called'] is False
    data = request(f'/tasks/{rid}/bundle')
    (out / (rid + '.zip')).write_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read('sha256.json'))
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest.items())
        assert json.loads(archive.read('report.json')) == report
    summary = {'run_id': rid, 'status': 'paused', 'counts': result['tool_result']['counts'],
               'model_api_called': False, 'final_result': 'not_run', 'files_verified': len(manifest),
               'bundle_sha256': hashlib.sha256(data).hexdigest(),
               'scope': 'real local HTTP and frozen baseline pytest only; not model repair'}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
