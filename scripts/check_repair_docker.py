"""Approved real Docker repair probes; never host-import candidate source."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.runner import code_version
from app.split_runner import SplitDockerRunner

GOOD = 'def is_leap(year):\n    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\n'
CASES = {
    'normal': GOOD,
    'fake_stdout': "print('tests passed=4')\n" + GOOD,
    'audit_overflow': "print('X' * (2 * 1024 * 1024))\n" + GOOD,
    'early_violation_then_overflow': "try:\n    open('/out/junit.xml','w').write('fake')\nexcept OSError:\n    pass\nprint('X' * (2 * 1024 * 1024))\n" + GOOD,
    'delayed_report_write': "import threading,time\ndef attack():\n    time.sleep(.1)\n    try:\n        open('/out/junit.xml','w').write('fake')\n    except OSError:\n        pass\nt = threading.Thread(target=attack)\nt.start()\nt.join()\n" + GOOD,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--image', default=os.environ.get('CODE_FIX_DOCKER_IMAGE', ''))
    args = parser.parse_args()
    folder = args.output_root.resolve() / ('docker-repair-' + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True)
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'image': args.image,
              'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
              'docker_executable': shutil.which('docker'), 'model_api_called': False,
              'scope': 'Synthetic real Split Docker security probes; not model repair acceptance',
              'cases': [], 'status': 'blocked'}
    runner = SplitDockerRunner()
    runner.image = args.image
    preflight = runner.preflight(folder / 'preflight')
    result['preflight'] = preflight
    if preflight['isolation_ready']:
        for name, source in CASES.items():
            work = runner.task.prepare(folder / (name + '-source'))
            (work / runner.task.source).write_bytes(source.encode())
            record = runner.run(work, folder / name, phase='development', timeout=30,
                                expected_version=code_version(work, (runner.task.source,)))
            passed = record.get('verification_status') == 'passed'
            result['cases'].append({'name': name, 'check_passed': passed if name == 'normal' else not passed,
                                    'result': record})
        result['status'] = 'passed' if all(c['check_passed'] for c in result['cases']) else 'failed'
    (folder / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    hashes = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in folder.rglob('*') if p.is_file() and not p.is_symlink()}
    (folder / 'sha256.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'folder': str(folder), 'preflight': preflight}, ensure_ascii=False))
    return {'passed': 0, 'failed': 1, 'blocked': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
