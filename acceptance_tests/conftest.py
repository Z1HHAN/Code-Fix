"""Offline acceptance gates: forbid accidental provider/process execution."""
from pathlib import Path
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('C acceptance gates must not start processes or real network connections')
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
