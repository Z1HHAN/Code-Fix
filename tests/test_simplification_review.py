"""Approved E-01/02/03/05 scope; preserve validation and byte identities."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.candidates import apply_candidate
from app.security import PermissionViolation
from app.tasks_v3 import get_task
import c_runner.runner as runner


def junction(target, link):
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize('position', ['root', 'ancestor', 'nested', 'empty'])
def test_snapshot_rejects_directory_links_before_descent(tmp_path, position):
    outside = tmp_path / 'outside'
    (outside / 'sub').mkdir(parents=True)
    if position != 'empty':
        (outside / 'sub/data.txt').write_bytes(b'must not be read')
    root = tmp_path / 'work'
    if position in {'root', 'ancestor'}:
        junction(outside, root)
        work = root / 'sub' if position == 'ancestor' else root
    else:
        root.mkdir()
        junction(outside, root / 'link')
        work = root
    original = Path.read_bytes
    def read(path):
        assert not path.resolve().is_relative_to(outside), 'must reject link before reading target'
        return original(path)
    with patch.object(Path, 'read_bytes', read), pytest.raises(runner.Rejected, match='Symlinks and junctions'):
        runner._snapshot(work)


def test_snapshot_keeps_exact_hashes_with_one_ancestor_check(tmp_path):
    (tmp_path / 'nested/empty').mkdir(parents=True)
    data = {'a.py': b'pass\r\n', 'nested/b.txt': b'\x00\r\nraw'}
    for path, raw in data.items():
        (tmp_path / path).write_bytes(raw)
    with patch.object(runner, '_no_links', wraps=runner._no_links) as check:
        assert runner._snapshot(tmp_path) == {p: hashlib.sha256(b).hexdigest() for p, b in data.items()}
        assert check.call_count == 1


def test_direct_hash_files_still_rejects_linked_ancestor(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'a.py').write_bytes(b'pass\n')
    root = tmp_path / 'work'
    root.mkdir()
    junction(outside, root / 'link')
    with pytest.raises(runner.Rejected, match='Symlinks and junctions'):
        runner._hash_files(root, ['link/a.py'])


def test_snapshot_rejects_file_link_flag(tmp_path, monkeypatch):
    file = tmp_path / 'data.py'
    file.write_bytes(b'pass\n')
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == file or original(p))
    with pytest.raises(runner.Rejected, match='Symlinks and junctions'):
        runner._snapshot(tmp_path)


def test_candidate_still_rejects_unregistered_empty_junction(tmp_path):
    task = get_task('leap')
    base = task.prepare(tmp_path / 'base')
    empty = tmp_path / 'empty'
    empty.mkdir()
    junction(empty, base / 'extra')
    destination = tmp_path / 'destination'
    with pytest.raises(runner.Rejected, match='Symlinks and junctions'):
        apply_candidate(base, destination, [{'path': task.source, 'new_content': 'pass\n'}], tmp_path / 'evidence')
    assert not destination.exists()


@pytest.mark.parametrize('changes', [None, [], [{'path': 'code/leap_year.py', 'new_content': 1}],
                                    [{'path': 'code/leap_year.py', 'new_content': 'pass', 'extra': True}]])
def test_candidate_structure_checks_remain_before_copy(tmp_path, changes):
    base = get_task('leap').prepare(tmp_path / 'base')
    with pytest.raises(PermissionViolation):
        apply_candidate(base, tmp_path / 'dest', changes, tmp_path / 'evidence')
    assert not (tmp_path / 'dest').exists()


def test_runtime_limits_cache_reuses_and_returns_independent_values(tmp_path, monkeypatch):
    import app.runtime_limits as limits
    from app.run_store import validate_task
    path = tmp_path / 'runtime_limits.json'
    path.write_text(json.dumps({'max_rounds': 3, 'max_calls': 30, 'total_seconds': 1800,
                                'max_tool_calls': 5, 'defaults': {'max_rounds': 3}}))
    monkeypatch.setattr(limits, 'LIMITS_PATH', path)
    limits._load.cache_clear()
    with patch.object(limits, 'read_json', wraps=limits.read_json) as read:
        first = limits.runtime_limits()
        first['defaults']['max_rounds'] = 999
        validate_task({})
        validate_task({})
        assert limits.runtime_limits()['defaults']['max_rounds'] == 3
        assert read.call_count == 1


def test_runtime_limits_cache_reload_and_missing_or_invalid_file_fail(tmp_path, monkeypatch):
    import app.runtime_limits as limits
    from app.config import ConfigError
    from app.errors import InputValidationError
    from app.run_store import validate_task
    path = tmp_path / 'limits.json'
    monkeypatch.setattr(limits, 'LIMITS_PATH', path)
    limits._load.cache_clear()
    path.write_text(json.dumps({'max_rounds': 3, 'max_calls': 30, 'total_seconds': 1800}))
    assert validate_task({'max_calls': 30})['max_calls'] == 30
    # Force a distinct metadata identity even on coarse-time filesystems.
    stamp = path.stat().st_mtime_ns
    path.write_text(json.dumps({'max_rounds': 3, 'max_calls': 2, 'total_seconds': 1800}))
    os.utime(path, ns=(stamp+2_000_000_000, stamp+2_000_000_000))
    with pytest.raises(InputValidationError):
        validate_task({'max_calls': 30})
    path.write_text('not JSON')
    with pytest.raises(ConfigError):
        limits.runtime_limits()
    path.unlink()
    with pytest.raises(FileNotFoundError):
        limits.runtime_limits()
