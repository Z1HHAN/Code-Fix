"""Closed, bounded JSON object graph. Never imports a class named by a peer.

Only the registered Node class and builtin containers are admitted. Object IDs
preserve aliases/cycles and let trusted callers apply mutations to their own
objects, after validating the complete response. This is not pickle.
"""
import json
import math
from collections import defaultdict
from types import FunctionType

_FLOAT = float
_infinity_factory = lambda: float('inf')


def _factory_tag(factory):
    """Recognize the native constant factory without calling candidate code."""
    if factory is None:
        return 'missing'
    if type(factory) is FunctionType:
        code, fixed = factory.__code__, _infinity_factory.__code__
        attributes = ('co_code', 'co_consts', 'co_names', 'co_varnames',
                      'co_freevars', 'co_cellvars', 'co_argcount',
                      'co_kwonlyargcount', 'co_posonlyargcount')
        if (all(getattr(code, name) == getattr(fixed, name) for name in attributes)
                and factory.__closure__ is None and factory.__defaults__ is None
                and factory.__kwdefaults__ is None
                and factory.__globals__.get('float', factory.__builtins__.get('float')) is _FLOAT):
            return 'positive_infinity'
    raise ValueError('unapproved defaultdict factory')

VERSION = 2
MAX_BYTES = 65536
MAX_OBJECTS = 1024
MAX_ITEMS = 16384
MAX_DEPTH = 64
NODE_FIELDS = ('value', 'successor', 'successors', 'predecessors', 'incoming_nodes', 'outgoing_nodes')
NODE_FUNCTIONS = frozenset({'breadth_first_search', 'depth_first_search', 'detect_cycle',
    'reverse_linked_list', 'shortest_path_length', 'topological_ordering'})
TYPED_FUNCTIONS = NODE_FUNCTIONS | frozenset({'hanoi', 'minimum_spanning_tree',
    'shortest_paths', 'shortest_path_lengths'})


class Node:
    """Fixed candidate-side counterpart of the hash-bound interface class."""
    # Intentional shared defaults: the pinned native Node has this behavior.
    # Replacing them with fresh lists would silently change candidate semantics.
    def __init__(self, value=None, successor=None, successors=[], predecessors=[],
                 incoming_nodes=[], outgoing_nodes=[]):
        self.value = value
        self.successor = successor
        self.successors = successors
        self.predecessors = predecessors
        self.incoming_nodes = incoming_nodes
        self.outgoing_nodes = outgoing_nodes


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def loads(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('RPC byte limit')
    def invalid_constant(value):
        raise ValueError('untagged nonfinite number')
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant)
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_ITEMS * 4:
            raise ValueError('RPC JSON complexity limit')
        if type(item) is float and not math.isfinite(item):
            raise ValueError('untagged nonfinite number')
        if type(item) is dict:
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
    return value


def dumps(value):
    raw = json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
    if len(raw) > MAX_BYTES:
        raise ValueError('RPC byte limit')
    return raw


def pack(root, node_type=None, *, seeds=(), allow_defaultdict=False):
    objects = list(seeds)
    index = {id(value): i for i, value in enumerate(objects)}
    if len(index) != len(objects) or len(objects) > MAX_OBJECTS:
        raise ValueError('invalid RPC seed table')
    depths = [0] * len(objects)
    count = 0

    def atom(value, depth):
        nonlocal count
        count += 1
        if count > MAX_ITEMS or depth > MAX_DEPTH:
            raise ValueError('RPC complexity limit')
        kind = type(value)
        if kind in (type(None), bool, int, str):
            return value
        if kind is float:
            if math.isnan(value):
                raise ValueError('NaN is not an approved value')
            return value if math.isfinite(value) else {'float': 'inf' if value > 0 else '-inf'}
        if (kind not in (list, tuple, dict, set)
                and not (allow_defaultdict and kind is defaultdict)
                and (node_type is None or kind is not node_type)):
            raise ValueError('unapproved RPC value type')
        key = id(value)
        if key not in index:
            if len(objects) >= MAX_OBJECTS:
                raise ValueError('RPC object limit')
            index[key] = len(objects)
            objects.append(value)
            depths.append(depth)
        return {'ref': index[key]}

    encoded_root = atom(root, 0)
    rows = []
    i = 0
    while i < len(objects):
        value, depth = objects[i], depths[i] + 1
        kind = type(value)
        if node_type is not None and kind is node_type:
            if set(vars(value)) != set(NODE_FIELDS):
                raise ValueError('unapproved Node fields')
            row = {'type': 'node', 'items': [atom(vars(value)[name], depth) for name in NODE_FIELDS]}
        elif kind is dict or (allow_defaultdict and kind is defaultdict):
            row = {'type': 'dict', 'items': [[atom(k, depth), atom(v, depth)] for k, v in value.items()]}
            if kind is defaultdict:
                row.update(type='defaultdict', factory=_factory_tag(value.default_factory))
        elif kind in (list, tuple, set):
            row = {'type': kind.__name__, 'items': [atom(v, depth) for v in value]}
        else:
            raise ValueError('unapproved RPC seed type')
        rows.append(row)
        i += 1
    packet = {'version': VERSION, 'root': encoded_root, 'objects': rows}
    dumps(packet)
    return packet, objects


def unpack(packet, node_type=None, *, existing=(), original=None, allow_defaultdict=False):
    """Validate first, then commit mutations. Immutable input tuples cannot change."""
    if (type(packet) is not dict or set(packet) != {'version', 'root', 'objects'}
            or type(packet['version']) is not int or packet['version'] != VERSION
            or type(packet['objects']) is not list or len(packet['objects']) > MAX_OBJECTS):
        raise ValueError('invalid RPC graph envelope')
    dumps(packet)
    rows = packet['objects']
    expected_types = {'list': list, 'tuple': tuple, 'set': set, 'dict': dict, 'node': node_type}
    if allow_defaultdict:
        expected_types['defaultdict'] = defaultdict
    count = 0

    def check_atom(value):
        nonlocal count
        count += 1
        if count > MAX_ITEMS:
            raise ValueError('RPC item limit')
        if type(value) in (type(None), bool, int, str):
            return
        if type(value) is float and math.isfinite(value):
            return
        if type(value) is dict and set(value) == {'float'} and value['float'] in ('inf', '-inf'):
            return
        if (type(value) is dict and set(value) == {'ref'} and type(value['ref']) is int
                and 0 <= value['ref'] < len(rows)):
            return
        raise ValueError('invalid RPC atom or reference')

    check_atom(packet['root'])
    for row in rows:
        if (type(row) is not dict
                or set(row) != ({'type', 'items', 'factory'} if row.get('type') == 'defaultdict' else {'type', 'items'})
                or type(row['type']) is not str or row['type'] not in expected_types
                or expected_types[row['type']] is None or type(row['items']) is not list):
            raise ValueError('unapproved RPC object record')
        if row['type'] == 'defaultdict' and row['factory'] not in ('missing', 'positive_infinity'):
            raise ValueError('unapproved defaultdict factory')
        if row['type'] == 'node' and len(row['items']) != len(NODE_FIELDS):
            raise ValueError('invalid Node field count')
        for value in row['items']:
            if row['type'] in ('dict', 'defaultdict'):
                if type(value) is not list or len(value) != 2:
                    raise ValueError('invalid RPC dictionary pair')
                check_atom(value[0]); check_atom(value[1])
            else:
                check_atom(value)
    if existing:
        if (original is None or len(rows) < len(existing)
                or len(original['objects']) != len(existing)):
            raise ValueError('missing original RPC objects')
        for i, value in enumerate(existing):
            if type(value) is not expected_types[rows[i]['type']]:
                raise ValueError('RPC input object type changed')
            if type(value) is tuple and rows[i] != original['objects'][i]:
                raise ValueError('immutable RPC tuple changed')
        # A rejected response must leave the trusted inputs byte-for-byte intact.
        unpack(packet, node_type, allow_defaultdict=allow_defaultdict)

    objects = [None] * len(rows)
    for i, row in enumerate(rows):
        if i < len(existing):
            objects[i] = existing[i]
        elif row['type'] != 'tuple':
            constructor = expected_types[row['type']]
            objects[i] = object.__new__(constructor) if row['type'] == 'node' else constructor()
    building = set()

    def atom(value, depth=0):
        if depth > MAX_DEPTH:
            raise ValueError('RPC tuple depth limit')
        if type(value) is not dict:
            return value
        if 'float' in value:
            return float(value['float'])
        i = value['ref']
        if objects[i] is None:
            if i in building:
                raise ValueError('cyclic immutable RPC tuple')
            building.add(i)
            objects[i] = tuple(atom(v, depth + 1) for v in rows[i]['items'])
            building.remove(i)
        return objects[i]

    # Build every value (including unreachable rows) before any mutation.
    # A small tuple DAG can expand to exponential work when Python hashes it.
    # Bound expanded key work, not just the number of wire objects.
    hash_costs = {}
    hash_work = 0

    def check_key(value):
        nonlocal hash_work
        def cost(item):
            if type(item) is tuple:
                key = id(item)
                if key not in hash_costs:
                    total = 1
                    for child in item:
                        total += cost(child)
                        if total > MAX_ITEMS:
                            raise ValueError('RPC key complexity limit')
                    hash_costs[key] = total
                return hash_costs[key]
            if type(item) in (list, dict, set, defaultdict):
                raise ValueError('unhashable RPC key')
            return 1
        hash_work += cost(value)
        if hash_work > MAX_ITEMS:
            raise ValueError('RPC key complexity limit')

    prepared = []
    for i, row in enumerate(rows):
        kind = row['type']
        if kind == 'tuple':
            atom({'ref': i}); prepared.append(None)
        elif kind in ('dict', 'defaultdict'):
            value = {}
            for key, item in row['items']:
                key, item = atom(key), atom(item)
                check_key(key)
                try:
                    if key in value:
                        raise ValueError('duplicate RPC dictionary key')
                    value[key] = item
                except TypeError as exc:
                    raise ValueError('unhashable RPC dictionary key') from exc
            prepared.append(value)
        elif kind == 'set':
            for item in row['items']:
                check_key(atom(item))
            try:
                value = set(atom(v) for v in row['items'])
            except TypeError as exc:
                raise ValueError('unhashable RPC set member') from exc
            if len(value) != len(row['items']):
                raise ValueError('duplicate RPC set member')
            prepared.append(value)
        else:
            prepared.append([atom(v) for v in row['items']])
    result = atom(packet['root'])
    for obj, row, value in zip(objects, rows, prepared):
        if row['type'] == 'list':
            obj[:] = value
        elif row['type'] in ('dict', 'set', 'defaultdict'):
            obj.clear(); obj.update(value)
            if row['type'] == 'defaultdict':
                obj.default_factory = _infinity_factory if row['factory'] == 'positive_infinity' else None
        elif row['type'] == 'node':
            vars(obj).update(zip(NODE_FIELDS, value))
    return result, objects
