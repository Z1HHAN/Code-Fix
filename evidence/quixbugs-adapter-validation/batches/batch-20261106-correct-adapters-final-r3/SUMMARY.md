# QuixBugs correct 版本 · RPC 适配器加固验证

- 批次：`batch-20261106-correct-adapters-final-r3`
- 上游：`https://github.com/jkoppel/QuixBugs`
- 固定提交：`4257f44b0ff1181dedaedee6a447e133219fcebf`
- 验证方式：固定 Git 对象 SHA + `git cat-file` 原始字节 + 现有双容器 RPC 路径
- 模型调用：`false`；API 费用：`0 CNY`
- Docker 镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`
- 任务数：`29`
- 生产 profile 直接通过：`29/29`
- 进程内 skip 注入：`0`（两题已在生产 profile 精确登记，无需注入）
- RPC 适配器契约通过：`29/29`

| 题目 | 生产 profile | 验证方式 | passed | skipped | 适配器结论 |
|---|---|---|---:|---:|---|
| `qb-bitcount` | 通过 | production | 9 | 0 | 通过 |
| `qb-bucketsort` | 通过 | production | 7 | 0 | 通过 |
| `qb-find_first_in_sorted` | 通过 | production | 7 | 0 | 通过 |
| `qb-find_in_sorted` | 通过 | production | 7 | 0 | 通过 |
| `qb-flatten` | 通过 | production | 7 | 0 | 通过 |
| `qb-gcd` | 通过 | production | 6 | 0 | 通过 |
| `qb-get_factors` | 通过 | production | 11 | 0 | 通过 |
| `qb-is_valid_parenthesization` | 通过 | production | 3 | 0 | 通过 |
| `qb-kheapsort` | 通过 | production | 4 | 0 | 通过 |
| `qb-knapsack` | 通过 | production | 9 | 1 | 通过 |
| `qb-kth` | 通过 | production | 7 | 0 | 通过 |
| `qb-lcs_length` | 通过 | production | 9 | 0 | 通过 |
| `qb-levenshtein` | 通过 | production | 6 | 1 | 通过 |
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

> 说明：`qb-knapsack` 与 `qb-levenshtein` 的官方原生测试各含一个明确的慢测 `pytest.skip`。
> 精确 nodeid 已按用户批准（2026-11-06）登记在生产 profile 的 allowed_skips 中；
> 未登记的 skip 仍会被准入策略阻断，反向对照仍要求整体判定失败。

## 反向对照（官方坏版本必须失败）

| 题目 | 坏版本 blob | failure_class | passed | 结论 |
|---|---|---|---:|---|
| `qb-flatten` | `e3e79333670d` | code_runtime_error | 1 | 按预期失败 |
| `qb-kheapsort` | `967926dda15e` | assertion_failed | 1 | 按预期失败 |

> 反向对照证明 `MATERIALIZE_AS_LIST` 只是照搬原生 `list(...)` 语义，不会把坏版本判成通过。
