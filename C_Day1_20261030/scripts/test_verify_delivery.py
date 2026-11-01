"""Regression checks for delivery metadata; never execute the archived target code."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]


class DeliveryVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='code-fix-c-delivery-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'C_Day1_20261030'
        shutil.copytree(PACKAGE, self.root, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))

    def verify(self, *arguments):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(self.root / 'scripts/verify_delivery.py'), *arguments],
            capture_output=True, text=True, encoding='utf-8', errors='replace', check=False,
        )

    def bytes_snapshot(self, root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}

    def test_freeze_repairs_stale_index_without_including_caches(self):
        (self.root / 'handoff/文件索引.md').write_bytes(b'# Stale index\n\n- [missing](../missing.txt)\n')
        for directory in ('.pytest_cache', 'runs/local/.pytest_cache', '__pycache__'):
            cache = self.root / directory
            cache.mkdir(parents=True, exist_ok=True)
            (cache / 'ignored.txt').write_bytes(b'not delivered')
        original_runs = self.bytes_snapshot(self.root / 'runs')
        result = self.verify('--freeze')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.root / 'handoff/文件校验清单.json').read_text(encoding='utf-8'))
        self.assertFalse(any('.pytest_cache' in name or '__pycache__' in name for name in data['sha256']))
        self.assertNotIn('../missing.txt', (self.root / 'handoff/文件索引.md').read_text(encoding='utf-8'))
        self.assertEqual(self.bytes_snapshot(self.root / 'runs'), original_runs)
        self.assertEqual(self.verify().returncode, 0)

    def test_plain_verification_is_read_only_and_rejects_changed_bytes(self):
        before = self.bytes_snapshot(self.root)
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.bytes_snapshot(self.root), before)
        source = self.root / 'code/leap_year.py'
        source.write_bytes(source.read_bytes() + b'\n')
        changed = self.bytes_snapshot(self.root)
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('File set or SHA256 mismatch', result.stderr)
        self.assertEqual(self.bytes_snapshot(self.root), changed)

    def test_unlisted_delivery_file_is_rejected(self):
        (self.root / 'unreviewed.txt').write_bytes(b'new delivery content')
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('File set or SHA256 mismatch', result.stderr)


if __name__ == '__main__':
    unittest.main()
