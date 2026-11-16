def next_palindrome(digit_list):
    n = len(digit_list)
    # next palindrome strictly greater than the current one
    # work on a copy and only mirror once we know the increment location
    for i in range((n - 1) // 2, -1, -1):
        if digit_list[i] < 9:
            digit_list[i] += 1
            mirror_index = n - 1 - i
            if mirror_index != i:
                digit_list[mirror_index] = digit_list[i]
            # reset all inner digits (between i and mirror) to 0
            for j in range(i + 1, n - 1 - i):
                digit_list[j] = 0
            return digit_list
        else:
            digit_list[i] = 0
            mirror_index = n - 1 - i
            if mirror_index != i:
                digit_list[mirror_index] = 0
    # all digits were 9 -> number grows by one digit
    result = [1] + [0] * n + [1]
    for i in range(n):
        digit_list[i] = 0
    return result

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
