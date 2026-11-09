"""Trusted pure JSON function proxy. Never imports or deserializes candidate code."""
import json
import socket
import time

def invoke(function, args, kwargs):
    request = json.dumps({'function': function, 'args': args, 'kwargs': kwargs}, allow_nan=False).encode() + b'\n'
    if len(request) > 65536:
        raise ValueError('RPC input exceeds bound')
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        for attempt in range(100):
            try:
                client.connect('/rpc/channel.sock')
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if attempt == 99:
                    raise
                time.sleep(.02)
        client.sendall(request)
        with client.makefile('rb') as stream:
            raw = stream.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            raise ValueError('RPC response exceeds bound or is incomplete')
        result = json.loads(raw)
        if not isinstance(result, dict) or set(result) != {'value'}:
            raise RuntimeError('candidate RPC failed')
        return result['value']

def max_sublist_sum(*args, **kwargs):
    return invoke('max_sublist_sum', args, kwargs)
