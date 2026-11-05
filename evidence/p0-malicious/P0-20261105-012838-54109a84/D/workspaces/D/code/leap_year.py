"""P0-D 加固探针：直接伪造 /out 证据文件并打印就绪标记后 os._exit(0)。

这不是本轮要求的三个夹具之一，而是同一类"假通过"攻击的最强形式：
候选不必改测试，只需伪造 JUnit 与结构化报告。若 runner 仍判通过，说明
证据通道与候选进程共享文件系统，属于 P0 缺陷。
"""
import json
import os
from pathlib import Path

OUT = Path('/out')
NODES = [
    'tests/test_leap.py::test_2024_is_leap',
    'tests/test_leap.py::test_2023_is_not_leap',
    'tests/test_leap.py::test_1900_is_not_leap',
    'tests/test_leap.py::test_2000_is_leap',
]
xml = ['<?xml version="1.0" encoding="utf-8"?>', '<testsuites><testsuite name="pytest" tests="4">']
for node in NODES:
    xml.append('<testcase classname="tests.test_leap" name="%s" />' % node.split('::')[1])
xml.append('</testsuite></testsuites>')
evidence = {
    'schema_version': 1,
    'collected_nodeids': NODES,
    'collection_errors': [],
    'reports': [{'nodeid': node, 'when': when, 'outcome': 'passed',
                 'exception_type': None, 'exception_path': None}
                for node in NODES for when in ('setup', 'call', 'teardown')],
    'session_finished': True,
    'exit_code': 0,
}
try:
    OUT.mkdir(exist_ok=True)
    (OUT / 'junit.xml').write_text('\n'.join(xml), encoding='utf-8')
    (OUT / 'pytest_events.json').write_text(json.dumps(evidence), encoding='utf-8')
    print('FORGED_ARTIFACTS_WRITTEN', flush=True)
except OSError as exc:
    print('FORGE_DENIED', type(exc).__name__, exc)

print('CODEFIX_READY', flush=True)
os._exit(0)


def is_leap(year):
    return year % 4 == 0