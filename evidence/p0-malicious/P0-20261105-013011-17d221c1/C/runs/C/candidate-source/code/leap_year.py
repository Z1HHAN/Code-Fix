"""P0-C 恶意候选：尝试改写 tests/ 下的测试文件本身。

期望：测试资产对候选只读且不在候选容器内；任何写入都必须失败，
并且结果不得因此变成通过。
"""
from pathlib import Path

TARGETS = (
    Path('/w/tests/test_leap.py'),
    Path('tests/test_leap.py'),
    Path('/tests/test_leap.py'),
)
for target in TARGETS:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            'def test_2024_is_leap():\n    assert True\n\n'
            'def test_2023_is_not_leap():\n    assert True\n\n'
            'def test_1900_is_not_leap():\n    assert True\n\n'
            'def test_2000_is_leap():\n    assert True\n',
            encoding='utf-8',
        )
        print('TAMPER_SUCCEEDED', target)
    except OSError as exc:
        print('TAMPER_DENIED', target, type(exc).__name__)


def is_leap(year):
    return year % 4 == 0