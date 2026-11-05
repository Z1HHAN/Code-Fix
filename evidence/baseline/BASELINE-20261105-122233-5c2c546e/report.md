# 四道题原始坏版本 · 容器内基线实测

运行编号：`BASELINE-20261105-122233-5c2c546e`；runner：`SplitDockerRunner`；镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`；超时：90s。

| 题目 | 源文件 | 源 SHA256（前 12 位） | 容器退出码 | tests | failures | errors | failure_class | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| leap | `code/leap_year.py` | `0ec1efe58ae5` | 1 | 4 | 1 | 0 | `assertion_failed` | 符合预期（真实失败） |
| qb-get_factors | `python_programs/get_factors.py` | `7eff7ef2adbb` | 1 | 11 | 10 | 0 | `assertion_failed` | 符合预期（真实失败） |
| qb-gcd | `python_programs/gcd.py` | `d68e155c2af4` | 1 | 6 | 5 | 0 | `code_runtime_error` | 符合预期（真实失败） |
| qb-sqrt | `python_programs/sqrt.py` | `e3623a160e6a` | 1 | 7 | 7 | 0 | `code_runtime_error` | 符合预期（真实失败） |

## 失败原因摘录

### leap

- runner 结论：Frozen native fixture tests completed with a recorded failure
- 失败归因：断言结果与预期不符
- `tests.test_leap::test_1900_is_not_leap`：assert True is False

### qb-get_factors

- runner 结论：Frozen native fixture tests completed with a recorded failure
- 失败归因：断言结果与预期不符
- `python_testcases.test_get_factors::test_get_factors[input_data1-expected1]`：AssertionError: assert [2, 2, 5] == [2, 2, 5, 5]
- `python_testcases.test_get_factors::test_get_factors[input_data2-expected2]`：assert [] == [101]
- `python_testcases.test_get_factors::test_get_factors[input_data3-expected3]`：AssertionError: assert [2, 2, 2] == [2, 2, 2, 13]

### qb-gcd

- runner 结论：Frozen native fixture tests completed with a recorded failure
- 失败归因：被测函数递归不收敛，候选进程抛出 RecursionError
- `python_testcases.test_gcd::test_gcd[input_data1-13]`：RuntimeError: candidate RPC failed
- `python_testcases.test_gcd::test_gcd[input_data2-1]`：RuntimeError: candidate RPC failed
- `python_testcases.test_gcd::test_gcd[input_data3-20]`：RuntimeError: candidate RPC failed

### qb-sqrt

- runner 结论：Frozen native fixture tests completed with a recorded failure
- 失败归因：被测函数不返回（死循环），单次 RPC 调用超时；runner 整体未超时且清理通过
- `python_testcases.test_sqrt::test_sqrt[input_data0-1.4166666666666665]`：TimeoutError: timed out
- `python_testcases.test_sqrt::test_sqrt[input_data1-1.5]`：TimeoutError: timed out
- `python_testcases.test_sqrt::test_sqrt[input_data2-1.5]`：TimeoutError: timed out

## 结论

四道题均在真实 Docker 中按预期失败，失败来自被测代码断言/运行时错误，无超时与环境错误。
