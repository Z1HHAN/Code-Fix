"""Untrusted endpoint: no test assets or evidence output are mounted here."""
import contextlib
import importlib.util
import json
import os
import socket
from pathlib import Path
import types
import sys

# The controller and codec are separate fixed read-only mounts. No candidate
# path participates in resolving this module, including under Python -I.
_codec_path = Path('/rpc_codec.py') if Path('/rpc_codec.py').is_file() else Path(__file__).with_name('rpc_codec.py')
_codec_spec = importlib.util.spec_from_file_location('_codefix_rpc_codec', _codec_path)
codec = importlib.util.module_from_spec(_codec_spec)
_codec_spec.loader.exec_module(codec)

AUDIT_PREFIX = 'CODEFIX_AUDIT '
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_PATH_EVENTS = {'open', 'os.open', 'os.remove', 'os.unlink', 'os.rename', 'os.mkdir',
                'os.rmdir', 'os.truncate', 'os.link', 'os.symlink'}
_spawning = []


def _emit(event, detail=None):
    try:
        payload = {'event': event}
        if detail is not None:
            payload['detail'] = detail
        line = AUDIT_PREFIX + json.dumps(payload, ensure_ascii=False, default=str) + '\n'
        os.write(2, line.encode('utf-8', 'replace')[:4096])
    except BaseException:
        pass


def _classify_path(path):
    try:
        text = os.fsdecode(os.fspath(path)).replace('\\', '/')
    except TypeError:
        return None
    parts = [x for x in text.split('/') if x]
    if text == '/out' or text.startswith('/out/'):
        return 'artifact_write'
    if ('/tests/' in text or text.endswith('/tests') or text.startswith('tests/')
            or (parts and (parts[-1] == 'tests' or parts[-1].startswith('test_')))):
        return 'tests_write'
    return None


def _audit(event, args):
    if _spawning:
        return
    _spawning.append(1)
    try:
        if event in ('os.fork', 'os.forkpty'):
            _emit('process_fork')
        elif event in ('subprocess.Popen', 'os.exec', 'os.posix_spawn', 'os.system', 'os.spawn'):
            _emit('process_spawn', event)
        elif event in _PATH_EVENTS and args:
            path = args[0]
            write = event != 'open'
            if event == 'open':
                mode = args[1] if len(args) > 1 else None
                flags = args[2] if len(args) > 2 else 0
                if isinstance(mode, str) and any(ch in mode for ch in 'wax+'):
                    write = True
                elif isinstance(flags, int) and flags & _WRITE_FLAGS:
                    write = True
            if write:
                kind = _classify_path(path)
                if kind:
                    try:
                        _emit(kind, os.fsdecode(os.fspath(path)))
                    except TypeError:
                        _emit(kind)
    except BaseException:
        pass
    finally:
        _spawning.pop()


# This standalone entry has no access to the host app package. New adapters
# must register their reviewed name here and in app.split_runner.FUNCTIONS.
ALLOWED_FUNCTIONS = frozenset({
    'topological_ordering',
    'shortest_path_length',
    'reverse_linked_list',
    'detect_cycle',
    'depth_first_search',
    'breadth_first_search',
    'shortest_path_lengths',
    'shortest_paths',
    'minimum_spanning_tree',
    'hanoi',
    'shunting_yard',
    'bitcount',
    'bucketsort',
    'find_first_in_sorted',
    'find_in_sorted',
    'flatten',
    'gcd',
    'get_factors',
    'is_leap',
    'is_valid_parenthesization',
    'kheapsort',
    'knapsack',
    'kth',
    'lcs_length',
    'levenshtein',
    'lis',
    'longest_common_subsequence',
    'max_sublist_sum',
    'mergesort',
    'next_palindrome',
    'next_permutation',
    'pascal',
    'possible_change',
    'powerset',
    'quicksort',
    'rpn_eval',
    'sieve',
    'sqrt',
    'subsequences',
    'to_base',
    'wrap',
})

# Mirrors the pinned native assertion `list(f(...))` for these generator
# entries.  Declared per function and pinned to app.split_runner by a
# regression test; returns of every other function are never coerced.
MATERIALIZE_AS_LIST = frozenset({
    'flatten',
    'kheapsort',
})


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
        value = getattr(module, request['function'])(*request['args'], **request['kwargs'])
        if request['function'] in MATERIALIZE_AS_LIST:
            # The pinned test compares `list(f(...))`; evaluate the same
            # expression here so a generator can cross the JSON boundary.
            value = list(value)
        return value


def execute_wire_request(request, source):
    # Reject functions and malformed graphs before importing candidate bytes.
    if (type(request) is not dict or type(request.get('function')) is not str
            or request['function'] not in ALLOWED_FUNCTIONS):
        raise ValueError('unapproved RPC request')
    function = request['function']
    if function not in codec.TYPED_FUNCTIONS:
        return {'value': execute_request(request, source)}
    if set(request) != {'function', 'payload'}:
        raise ValueError('invalid typed RPC request')
    node_type = codec.Node if function in codec.NODE_FUNCTIONS else None
    inputs, objects = codec.unpack(request['payload'], node_type)
    if (type(inputs) is not list or len(inputs) != 2 or type(inputs[0]) is not list
            or type(inputs[1]) is not dict or any(type(k) is not str for k in inputs[1])):
        raise ValueError('invalid typed RPC arguments')
    if node_type is not None:
        interface = types.ModuleType('node')
        interface.Node = codec.Node
        sys.modules['node'] = interface
    value = execute_request({'function': function, 'args': inputs[0], 'kwargs': inputs[1]}, source)
    payload, _ = codec.pack(value, node_type, seeds=objects)
    return {'payload': payload}


def main():
    sys.addaudithook(_audit)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind('/rpc/channel.sock')
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection, connection.makefile('rb') as stream:
                try:
                    raw = stream.readline(65537)
                    if len(raw) > 65536 or not raw.endswith(b'\n'):
                        raise ValueError('invalid RPC input length')
                    value = execute_wire_request(codec.loads(raw), sys.argv[1])
                    response = codec.dumps(value)
                    if len(response) > 65536:
                        raise ValueError('response too large')
                except SystemExit as exc:
                    _emit('early_exit', {'code': exc.code if isinstance(exc.code, (int, type(None))) else 'non-int'})
                    response = b'{"error":"candidate_early_exit"}\n'
                except BaseException as exc:
                    _emit('execution_error', type(exc).__name__)
                    response = b'{"error":"candidate_execution_failed"}\n'
                connection.sendall(response)


if __name__ == '__main__':
    main()
