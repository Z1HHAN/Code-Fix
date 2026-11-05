"""Untrusted endpoint: no test assets or evidence output are mounted here."""
import contextlib
import importlib.util
import json
import socket
import sys

# This standalone entry has no access to the host app package. New adapters
# must register their reviewed name here and in app.split_runner.FUNCTIONS.
ALLOWED_FUNCTIONS = frozenset({'is_leap', 'get_factors', 'gcd', 'sqrt'})


def execute_request(request, source):
    # Validate before importing any candidate bytes, including module-level code.
    if (not isinstance(request, dict) or set(request) != {'function', 'args', 'kwargs'}
            or not isinstance(request['function'], str)
            or request['function'] not in ALLOWED_FUNCTIONS
            or not isinstance(request['args'], list)
            or not isinstance(request['kwargs'], dict)
            or any(not isinstance(key, str) for key in request['kwargs'])):
        raise ValueError('unapproved RPC request')
    spec = importlib.util.spec_from_file_location('candidate', source)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
        return getattr(module, request['function'])(*request['args'], **request['kwargs'])


def main():
    with socket.socket(socket.AF_UNIX) as server:
        server.bind('/rpc/channel.sock')
        server.listen(1)
        while True:
            connection, _ = server.accept()
            with connection, connection.makefile('rb') as stream:
                try:
                    raw = stream.readline(65537)
                    if len(raw) > 65536 or not raw.endswith(b'\n'):
                        raise ValueError('invalid RPC input length')
                    value = execute_request(json.loads(raw), sys.argv[1])
                    response = json.dumps({'value': value}, allow_nan=False).encode() + b'\n'
                    if len(response) > 65536:
                        raise ValueError('response too large')
                except Exception:
                    response = b'{"error":"candidate_execution_failed"}\n'
                connection.sendall(response)


if __name__ == '__main__':
    main()
