import importlib.util
from pathlib import Path

# 按路径加载任务代码，避免 code 目录与 Python 标准库 code 模块重名。
_source = Path(__file__).resolve().parents[1] / "code" / "leap_year.py"
_spec = importlib.util.spec_from_file_location("task_leap_year", _source)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
is_leap = _module.is_leap


def test_2024_is_leap():
    assert is_leap(2024) is True


def test_2023_is_not_leap():
    assert is_leap(2023) is False


def test_1900_is_not_leap():
    assert is_leap(1900) is False


def test_2000_is_leap():
    assert is_leap(2000) is True
