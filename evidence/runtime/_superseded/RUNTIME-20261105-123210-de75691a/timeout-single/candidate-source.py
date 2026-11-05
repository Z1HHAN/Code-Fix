"""死循环夹具A：模块导入即挂起，模拟完全无响应的候选。"""


def is_leap(year):
    return year % 4 == 0


while True:
    pass
