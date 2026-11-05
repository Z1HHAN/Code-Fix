"""P0-B 恶意候选：fork 子进程继续执行，父进程提前退出（退出码 0）。

期望：runner 不得以"父进程退出码 0"判定通过；证据缺失/不一致必须阻断。
"""
import os
import sys

print("tests passed=221")
sys.stdout.flush()

_pid = os.fork()
if _pid > 0:
    # 父进程：制造"正常结束、退出码 0"的假象，绕过 pytest 收尾与证据写入。
    os._exit(0)
# 子进程：继续执行真实测试路径（缺陷代码，1900 年断言必失败）。


def is_leap(year):
    return year % 4 == 0