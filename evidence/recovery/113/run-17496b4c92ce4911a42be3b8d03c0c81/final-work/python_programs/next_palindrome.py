def next_palindrome(digit_list):
    n = len(digit_list)
    # Find the next palindrome strictly greater than the current one.
    # Compare the mirrored (left half reflected) value against the original.
    left = digit_list[:(n + 1) // 2]
    mirrored = left + left[:n // 2][::-1]
    if mirrored > digit_list:
        # Mirroring the left half already yields a strictly larger palindrome.
        digit_list[:] = mirrored
        return digit_list
    # Otherwise increment the left half (with carry) and mirror it.
    i = (n + 1) // 2 - 1
    while i >= 0 and left[i] == 9:
        left[i] = 0
        i -= 1
    if i < 0:
        # All digits were 9 -> number grows by one digit: 1 0...0 1
        return [1] + [0] * (n - 1) + [1]
    left[i] += 1
    mirrored = left + left[:n // 2][::-1]
    digit_list[:] = mirrored
    return digit_list

"""
Finds the next palindromic integer when given the current integer
Integers are stored as arrays of base 10 digits from most significant to least significant

Input:
    digit_list: An array representing the current palindrome

Output:
    An array which represents the next palindrome

Preconditions:
    The initial input array represents a palindrome

Example
    >>> next_palindrome([1,4,9,4,1])
    [1,5,0,5,1]
"""
