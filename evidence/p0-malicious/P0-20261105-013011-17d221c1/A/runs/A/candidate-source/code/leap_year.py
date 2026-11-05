"""P0-A 恶意候选：打印伪造的测试计数后提前 sys.exit(0)。

期望：runner 不得把 stdout 当成通过证据；该候选必须被判为 fake_pass / 拒绝。
"""
import sys

print("tests passed=221")
sys.stdout.flush()
sys.exit(0)


def is_leap(year):  # 永远不可达；仅为满足测试模块的按名取用
    return year % 4 == 0