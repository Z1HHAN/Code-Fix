def is_leap(year):
    """手册提供的参考答案；仅用于验证测试环境。"""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
