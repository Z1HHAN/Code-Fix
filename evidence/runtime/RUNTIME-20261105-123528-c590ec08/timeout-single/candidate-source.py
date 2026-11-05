"""第4项夹具A：模块级死循环。

pytest 收集阶段 import 本模块会永久挂起，容器因此永远不会输出
CODEFIX_READY，只能由 runner 的超时看门狗终止（验证"超时后强杀容器"）。
"""


def is_leap(year):
    return year % 4 == 0


while True:
    pass