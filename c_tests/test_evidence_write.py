import json
import os

import pytest

from c_runner.pytest_entry import Evidence


def test_temporary_permission_error_retries_atomic_replace(tmp_path, monkeypatch):
    actual = os.replace
    attempts = []
    def busy(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError('fixed transient file lock')
        actual(source, destination)
    monkeypatch.setattr(os, 'replace', busy)
    evidence = Evidence(tmp_path / 'pytest_events.json')
    assert len(attempts) == 3
    assert json.loads(evidence.destination.read_text(encoding='utf-8'))['session_finished'] is False


def test_permanent_permission_error_is_bounded(tmp_path, monkeypatch):
    attempts = []
    def blocked(*args):
        attempts.append(1)
        raise PermissionError('fixed permanent file lock')
    monkeypatch.setattr(os, 'replace', blocked)
    with pytest.raises(PermissionError):
        Evidence(tmp_path / 'pytest_events.json')
    assert len(attempts) == 3
