from leap_app.leap_year import is_leap

def test_2024_is_leap():
    assert is_leap(2024) == True

def test_2023_is_not_leap():
    assert is_leap(2023) == False

def test_1900_is_not_leap():   # 被100整除但不被400整除
    assert is_leap(1900) == False

def test_2000_is_leap():       # 被400整除
    assert is_leap(2000) == True
