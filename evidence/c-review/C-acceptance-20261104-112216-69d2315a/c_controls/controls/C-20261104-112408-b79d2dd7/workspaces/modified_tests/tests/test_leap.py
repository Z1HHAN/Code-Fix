from leap_app.leap_year import is_leap


def test_2024_is_leap():
    assert is_leap(2024) is True


def test_2023_is_not_leap():
    assert is_leap(2023) is False


def test_1900_is_not_leap():
    assert is_leap(1900) is False


def test_2000_is_leap():
    assert is_leap(2000) is True

# Deliberate C control tamper; must be blocked before execution.
