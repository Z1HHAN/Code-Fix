"""Trusted pure JSON function proxy. Never imports or deserializes candidate code."""
import json
import importlib.util
from pathlib import Path
import socket
import time

_codec_path = Path('/rpc_codec.py') if Path('/rpc_codec.py').is_file() else Path(__file__).with_name('rpc_codec.py')
_codec_spec = importlib.util.spec_from_file_location('_codefix_rpc_codec', _codec_path)
codec = importlib.util.module_from_spec(_codec_spec)
_codec_spec.loader.exec_module(codec)


def invoke(function, args, kwargs):
    packet = objects = node_type = None
    if function in codec.TYPED_FUNCTIONS:
        if function in codec.NODE_FUNCTIONS:
            # Only the hash-bound helper prepared with the trusted native tests.
            from node import Node
            node_type = Node
        packet, objects = codec.pack([list(args), kwargs], node_type)
        request = codec.dumps({'function': function, 'payload': packet})
    else:
        request = codec.dumps({'function': function, 'args': args, 'kwargs': kwargs})
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
        result = codec.loads(raw)
        if packet is not None:
            if type(result) is not dict or set(result) != {'payload'}:
                raise RuntimeError('candidate typed RPC failed')
            return codec.unpack(result['payload'], node_type, existing=objects, original=packet,
                                allow_defaultdict=function == 'shortest_path_lengths')[0]
        if not isinstance(result, dict) or set(result) != {'value'}:
            raise RuntimeError('candidate RPC failed')
        return result['value']
