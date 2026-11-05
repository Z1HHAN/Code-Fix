"""第4项夹具B：函数内死循环。

模块可以正常导入、RPC 服务可以启动，但被调用函数永不返回，
用于验证可信测试侧按单次 RPC 上限收敛、runner 不会无限等待。
"""


def is_leap(year):
    while True:
        pass