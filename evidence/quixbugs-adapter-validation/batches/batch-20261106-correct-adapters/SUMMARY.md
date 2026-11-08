# QuixBugs correct 版本 · RPC 适配器加固验证

- 批次：`batch-20261106-correct-adapters`
- 上游：`https://github.com/jkoppel/QuixBugs`
- 固定提交：`4257f44b0ff1181dedaedee6a447e133219fcebf`
- 验证方式：固定 Git 对象 SHA + `git cat-file` 原始字节 + 现有双容器 RPC 路径
- 模型调用：`false`；API 费用：`0 CNY`
- Docker 镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`
- 任务数：`28`
- 生产 profile 直接通过：`25/28`
- 精确原生 skip 策略验证通过：`2`
- RPC 适配器契约通过：`25/28`

| 题目 | 生产 profile | 验证方式 | passed | skipped | 适配器结论 |
|---|---|---|---:|---:|---|
| `qb-bitcount` | 通过 | production | 9 | 0 | 通过 |
| `qb-bucketsort` | 通过 | production | 7 | 0 | 通过 |
| `qb-find_first_in_sorted` | 通过 | production | 7 | 0 | 通过 |
| `qb-find_in_sorted` | 通过 | production | 7 | 0 | 通过 |
| `qb-flatten` | 阻断/需策略 | not_needed | 0 | 0 | 失败 |
| `qb-gcd` | 通过 | production | 6 | 0 | 通过 |
| `qb-get_factors` | 通过 | production | 11 | 0 | 通过 |
| `qb-is_valid_parenthesization` | 通过 | production | 3 | 0 | 通过 |
| `qb-knapsack` | 阻断/需策略 | 精确原生 skip（内存策略） | 9 | 1 | 失败 |
| `qb-kth` | 通过 | production | 7 | 0 | 通过 |
| `qb-lcs_length` | 通过 | production | 9 | 0 | 通过 |
| `qb-levenshtein` | 阻断/需策略 | 精确原生 skip（内存策略） | 6 | 1 | 失败 |
| `qb-lis` | 通过 | production | 12 | 0 | 通过 |
| `qb-longest_common_subsequence` | 通过 | production | 10 | 0 | 通过 |
| `qb-max_sublist_sum` | 通过 | production | 6 | 0 | 通过 |
| `qb-mergesort` | 通过 | production | 14 | 0 | 通过 |
| `qb-next_palindrome` | 通过 | production | 5 | 0 | 通过 |
| `qb-next_permutation` | 通过 | production | 8 | 0 | 通过 |
| `qb-pascal` | 通过 | production | 5 | 0 | 通过 |
| `qb-possible_change` | 通过 | production | 10 | 0 | 通过 |
| `qb-powerset` | 通过 | production | 5 | 0 | 通过 |
| `qb-quicksort` | 通过 | production | 13 | 0 | 通过 |
| `qb-rpn_eval` | 通过 | production | 6 | 0 | 通过 |
| `qb-sieve` | 通过 | production | 6 | 0 | 通过 |
| `qb-sqrt` | 通过 | production | 7 | 0 | 通过 |
| `qb-subsequences` | 通过 | production | 12 | 0 | 通过 |
| `qb-to_base` | 通过 | production | 10 | 0 | 通过 |
| `qb-wrap` | 通过 | production | 5 | 0 | 通过 |

## 失败项

- `qb-flatten`：Frozen native fixture tests completed with a recorded failure
- `qb-knapsack`：Unapproved skipped tests or no actual passing tests
- `qb-levenshtein`：Unapproved skipped tests or no actual passing tests

> 说明：`qb-knapsack` 与 `qb-levenshtein` 的官方原生测试包含明确的慢测/长测 `pytest.skip`。
> 第二次验证只在进程内把实际跳过的精确 nodeid 登记为 allowed_skips，未修改仓库配置；
> 这样单独证明 RPC 适配器语义正确，同时保留生产准入策略待用户确认。
