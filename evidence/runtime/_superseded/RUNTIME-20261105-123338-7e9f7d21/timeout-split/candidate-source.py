"""死循环夹具B：函数调用不返回，模拟被测逻辑卡死。"""


def is_leap(year):
    while True:
        pass
