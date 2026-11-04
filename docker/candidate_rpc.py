"""Untrusted endpoint: no test assets or evidence output are mounted here."""
import contextlib
import importlib.util
import json
import socket
import sys
from pathlib import Path

server = socket.socket(socket.AF_UNIX)
server.bind('/rpc/channel.sock')
server.listen(1)
while True:
    connection, _ = server.accept()
    with connection, connection.makefile('rb') as stream:
        try:
            request = json.loads(stream.readline(65537))
            spec = importlib.util.spec_from_file_location('candidate', sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            with contextlib.redirect_stdout(sys.stderr):
                spec.loader.exec_module(module)
                value = getattr(module, request['function'])(*request['args'], **request['kwargs'])
            response = json.dumps({'value': value}, allow_nan=False).encode() + b'\n'
            if len(response) > 65536:
                raise ValueError('response too large')
        except Exception:
            response = b'{"error":"candidate_execution_failed"}\n'
        connection.sendall(response)
