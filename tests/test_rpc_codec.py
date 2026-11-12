"""Protocol tests use synthetic values/callables, never execute public candidates."""
from copy import deepcopy
import importlib.util
import io
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from collections import defaultdict

import pytest

from docker import rpc_codec as codec
from app.split_runner import FUNCTIONS, SplitDockerRunner


def test_registered_defaultdict_preserves_type_factory_missing_keys_and_aliases():
    value = defaultdict(lambda: float('inf'), {(0, 0): 0, (0, 1): float('-inf')})
    packet, _ = codec.pack([value, value], allow_defaultdict=True)
    restored, _ = codec.unpack(codec.loads(codec.dumps(packet)), allow_defaultdict=True)
    assert type(restored[0]) is defaultdict and restored[0] is restored[1]
    assert restored[0][0, 0] == 0 and restored[0][0, 1] == float('-inf')
    assert (1, 0) not in restored[0]
    assert restored[0][1, 0] == float('inf') and (1, 0) in restored[0]
    with pytest.raises(ValueError):
        codec.pack(value)
    with pytest.raises(ValueError):
        codec.unpack(packet)


def test_defaultdict_factory_is_never_executed_or_transmitted():
    called = []
    def arbitrary():
        called.append(True)
        return float('inf')
    with pytest.raises(ValueError, match='factory'):
        codec.pack(defaultdict(arbitrary), allow_defaultdict=True)
    assert not called
    packet, _ = codec.pack(defaultdict(None, {'key': 1}), allow_defaultdict=True)
    restored, _ = codec.unpack(packet, allow_defaultdict=True)
    assert restored.default_factory is None
    with pytest.raises(KeyError):
        restored['missing']
    bad = deepcopy(packet)
    bad['objects'][0]['factory'] = '__import__'
    with pytest.raises(ValueError, match='factory'):
        codec.unpack(bad, allow_defaultdict=True)


def test_defaultdict_rejects_overridden_float_without_calling_it():
    namespace = {'float': lambda _: 12}
    exec("factory = lambda: float('inf')", namespace)
    with pytest.raises(ValueError, match='factory'):
        codec.pack(defaultdict(namespace['factory']), allow_defaultdict=True)


def test_shortest_path_lengths_endpoint_preserves_defaultdict(monkeypatch):
    ep = endpoint()
    expected = defaultdict(lambda: float('inf'), {(0, 0): 0, (0, 1): 3})
    monkeypatch.setattr(ep, 'execute_request', lambda request, source: expected)
    packet, _ = codec.pack([[2, {(0, 1): 3}], {}])
    response = ep.execute_wire_request({'function': 'shortest_path_lengths', 'payload': packet}, 'synthetic')
    actual, _ = codec.unpack(response['payload'], allow_defaultdict=True)
    assert type(actual) is defaultdict and actual[1, 0] == float('inf')
    with pytest.raises(ValueError):
        ep.execute_wire_request({'function': 'hanoi', 'payload': packet}, 'synthetic')


@pytest.mark.parametrize('distance,passes', [(3, True), (99, False)])
def test_proxy_defaultdict_retains_native_assertion_result(monkeypatch, distance, passes):
    from docker import rpc_proxy as proxy
    ep = endpoint()
    expected = {(0, 0): 0, (0, 1): 3}
    # Synthetic callable; no candidate or reference implementation is imported.
    value = defaultdict(lambda: float('inf'), {(0, 0): 0, (0, 1): distance})
    monkeypatch.setattr(ep, 'execute_request', lambda request, source: value)
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, address): pass
        def sendall(self, raw):
            self.response = codec.dumps(ep.execute_wire_request(codec.loads(raw), 'synthetic'))
        def makefile(self, mode): return io.BytesIO(self.response)
    monkeypatch.setattr(proxy.socket, 'socket', lambda *args: Client())
    monkeypatch.setattr(proxy.socket, 'AF_UNIX', 1, raising=False)
    actual = proxy.invoke('shortest_path_lengths', (2, {(0, 1): 3}), {})
    assert type(actual) is defaultdict
    assert (actual == expected) is passes  # wrong values must still fail
    assert actual[1, 0] == float('inf')


def endpoint():
    spec = importlib.util.spec_from_file_location('reviewed_endpoint', Path('docker/candidate_rpc.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_node_constructor_matches_all_pinned_interfaces_without_executing_assets():
    from app.tasks_v3 import get_task
    module = ast.parse(Path('docker/rpc_codec.py').read_text(encoding='utf-8'))
    constructor = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Node').body
    constructor = next(n for n in constructor if isinstance(n, ast.FunctionDef))
    for function in codec.NODE_FUNCTIONS:
        task = get_task('qb-' + function)
        path = Path(task.profile['fixture_dir']) / 'python_testcases/node.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        native = tree.body[0].body[0]
        assert ast.dump(native, include_attributes=False) == ast.dump(constructor, include_attributes=False)
    a, b = codec.Node(), codec.Node()
    assert a.successors is b.successors and a.predecessors is b.predecessors
    assert a.successors is not a.predecessors
    explicit = codec.Node(successors=None)
    assert explicit.successors is None


def test_container_types_nonfinite_and_aliases():
    shared = [1, 2]
    value = [{('a', 'b'): float('inf'), (3, 4): float('-inf')}, {(1, 2), (3, 4)},
             [(1, 3), (1, 2)], shared, shared]
    packet, _ = codec.pack(value)
    actual, _ = codec.unpack(codec.loads(codec.dumps(packet)))
    assert actual == value
    assert actual[3] is actual[4]
    assert type(actual[2][0]) is tuple and type(actual[1]) is set


def test_identity_cycles_mutations_and_return_original():
    a, b = codec.Node('a'), codec.Node('b')
    a.successor = b
    b.successor = a
    a.successors = b.successors = [a, b]
    original, originals = codec.pack([[a, b], {}], codec.Node)
    args, remote = codec.unpack(codec.loads(codec.dumps(original)), codec.Node)
    ra, rb = args[0]
    assert rb.successor is ra and ra.successors is rb.successors
    rb.successor = None
    ra.successor = rb
    ra.successors.append(rb)
    response, _ = codec.pack(rb, codec.Node, seeds=remote)
    returned, _ = codec.unpack(codec.loads(codec.dumps(response)), codec.Node,
                               existing=originals, original=original)
    assert returned is b and b.successor is None and a.successor is b
    assert a.successors is b.successors and a.successors[-1] is b


@pytest.mark.parametrize('bad', [
    {'version': 2, 'root': {'ref': -1}, 'objects': []},
    {'version': 2, 'root': {'ref': True}, 'objects': [{'type': 'list', 'items': []}]},
    {'version': 2, 'root': None, 'objects': [{'type': '__import__', 'items': []}]},
    {'version': 2, 'root': None, 'objects': [{'type': 'node', 'items': []}]},
    {'version': 2, 'root': None, 'objects': [{'type': 'dict', 'items': [['a', 1], ['a', 2]]}]},
    {'version': 2, 'root': None, 'objects': [{'type': 'set', 'items': [1, True]}]},
    {'version': 2, 'root': {'ref': 0}, 'objects': [{'type': 'tuple', 'items': [{'ref': 0}]}]},
])
def test_reject_malformed_or_executable_shapes(bad):
    with pytest.raises(ValueError):
        codec.unpack(bad, codec.Node)


def test_rejected_response_never_partially_updates_inputs():
    original, objects = codec.pack([[1], (2, 3)])
    bad = deepcopy(original)
    bad['objects'][1]['items'] = [999]
    bad['objects'][2]['items'] = [999]
    with pytest.raises(ValueError, match='immutable'):
        codec.unpack(bad, existing=objects, original=original)
    assert objects[1] == [1] and objects[2] == (2, 3)
    bad = deepcopy(original)
    bad['objects'][1]['items'] = [999]
    bad['objects'].append({'type': 'dict', 'items': [['x', 1], ['x', 2]]})
    with pytest.raises(ValueError, match='duplicate'):
        codec.unpack(bad, existing=objects, original=original)
    assert objects[1] == [1]


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'NaN', b'Infinity', b'[' * 66 + b'0' + b']' * 66,
                                b'"' + b'x' * 65536 + b'"'], ids=['duplicate-key','nan','infinity','depth','bytes'])
def test_wire_limits_and_ambiguous_json(raw):
    with pytest.raises(ValueError):
        codec.loads(raw)


def test_size_type_and_object_limits():
    for value in [float('nan'), object(), [object()]]:
        with pytest.raises(ValueError):
            codec.pack(value)
    with pytest.raises(ValueError, match='object limit'):
        codec.pack([[] for _ in range(codec.MAX_OBJECTS)])


def test_small_wire_tuple_dag_cannot_cause_exponential_hash_work():
    rows = [{'type': 'tuple', 'items': [0]}]
    for i in range(24):
        rows.append({'type': 'tuple', 'items': [{'ref': i}, {'ref': i}]})
    rows.append({'type': 'dict', 'items': [[{'ref': 24}, 1]]})
    with pytest.raises(ValueError, match='key complexity'):
        codec.unpack({'version': 2, 'root': {'ref': 25}, 'objects': rows})


def test_numeric_overflow_cannot_smuggle_nonfinite_json():
    with pytest.raises(ValueError, match='nonfinite'):
        codec.loads(b'{"value":1e9999}')


@pytest.mark.parametrize('function', sorted(codec.TYPED_FUNCTIONS))
def test_all_new_typed_endpoints_validate_before_import_and_roundtrip(monkeypatch, function):
    monkeypatch.setitem(sys.modules, 'node', SimpleNamespace())
    ep = endpoint()
    calls = []
    def execute(module):
        calls.append(function)
        setattr(module, function, lambda *args, **kwargs: [(1, 2), {(3, 4)}])
    monkeypatch.setattr(ep.importlib.util, 'spec_from_file_location',
                        lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=execute)))
    monkeypatch.setattr(ep.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    with pytest.raises(ValueError):
        ep.execute_wire_request({'function': function, 'payload': {}}, 'must-not-import')
    assert calls == []
    packet, _ = codec.pack([[], {}])
    response = ep.execute_wire_request({'function': function, 'payload': packet}, 'synthetic')
    assert codec.unpack(response['payload'])[0] == [(1, 2), {(3, 4)}]
    assert calls == [function]


def test_all_forty_names_and_fixed_readonly_codec_mount(tmp_path):
    from app.tasks_v3 import catalog
    assert set(FUNCTIONS) == set(catalog())
    assert endpoint().ALLOWED_FUNCTIONS == frozenset(FUNCTIONS.values())
    runner = SplitDockerRunner('qb-reverse_linked_list')
    runner.executable = 'docker'
    command = runner._candidate_command('synthetic', tmp_path, 'volume')
    assert sum('target=/rpc_codec.py,readonly' in arg for arg in command) == 1
    assert '--read-only' in command and '--network=none' in command


def test_trusted_proxy_and_endpoint_exchange_preserves_original_node(monkeypatch):
    from docker import rpc_proxy as proxy
    ep = endpoint()
    monkeypatch.setitem(sys.modules, 'node', SimpleNamespace(Node=codec.Node))
    def execute(module):
        def reverse(head):
            tail = head.successor
            head.successor = None
            tail.successor = head
            return tail
        module.reverse_linked_list = reverse
    monkeypatch.setattr(ep.importlib.util, 'spec_from_file_location',
                        lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=execute)))
    monkeypatch.setattr(ep.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, address): assert address == '/rpc/channel.sock'
        def sendall(self, raw):
            self.response = codec.dumps(ep.execute_wire_request(codec.loads(raw), 'synthetic-only'))
        def makefile(self, mode): return io.BytesIO(self.response)
    monkeypatch.setattr(proxy.socket, 'socket', lambda *args: Client())
    monkeypatch.setattr(proxy.socket, 'AF_UNIX', 1, raising=False)
    first, second = codec.Node(1), codec.Node(2)
    first.successor = second
    result = proxy.invoke('reverse_linked_list', (first,), {})
    assert result is second and result.successor is first and first.successor is None


@pytest.mark.parametrize('function', sorted(FUNCTIONS.values()))
def test_every_registered_wire_dispatch_uses_the_expected_protocol(monkeypatch, function):
    from docker import rpc_proxy as proxy
    ep = endpoint()
    monkeypatch.setitem(sys.modules, 'node', SimpleNamespace(Node=codec.Node))
    def execute(module):
        setattr(module, function, lambda value, *, x: [value, x])
    monkeypatch.setattr(ep.importlib.util, 'spec_from_file_location',
                        lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=execute)))
    monkeypatch.setattr(ep.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, address): pass
        def sendall(self, raw):
            request = codec.loads(raw)
            assert set(request) == ({'function', 'payload'} if function in codec.TYPED_FUNCTIONS
                                    else {'function', 'args', 'kwargs'})
            self.response = codec.dumps(ep.execute_wire_request(request, 'synthetic-only'))
        def makefile(self, mode): return io.BytesIO(self.response)
    monkeypatch.setattr(proxy.socket, 'socket', lambda *args: Client())
    monkeypatch.setattr(proxy.socket, 'AF_UNIX', 1, raising=False)
    assert proxy.invoke(function, (3,), {'x': 4}) == [3, 4]
