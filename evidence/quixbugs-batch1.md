# QuixBugs 批量闭环 · Batch 1

- 模型：`deepseek-v4.1-flash-1017`（provider = `aliyun_maas`）
- 端点：阿里云百炼 OpenAI 兼容模式（工作区子域已脱敏）
- 日期：2026-11-06
- 方式：真实模型 API 调用 + Docker 隔离环境执行测试
- 参数：`--max-rounds 3 --max-calls 12 --total-seconds 600`
- 费用口径：项目账本**估算值**（CNY），非平台实时余额
- 密钥：API key 仅在进程环境变量中读取，未写入本文件、日志或报告

## 结果表

| 题目名 | 跑了几轮 | 是否修复 | final_result | 失败原因 | 费用 | 备注 |
|---|---|---|---|---|---|---|
| `qb-bitcount` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.02164 | `run-621329e9aa5b47e9828c2b0ebc1f4e4e` |
| `qb-breadth_first_search` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-236c4070545740b9a9d9c232847f43cc` |
| `qb-bucketsort` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.02217 | `run-4abb211a44db4c3db164ec4d5e8d21fe` |
| `qb-depth_first_search` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-7e74bcc50b1c4d1f9b5d14e15e84b389` |
| `qb-detect_cycle` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-ab720d0371144f9591dcdebdf56e78d2` |
| `qb-find_first_in_sorted` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.028246 | `run-6094d72a33284a3fa0af64e13de66d2f` |
| `qb-find_in_sorted` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.026006 | `run-9cb592a5b7814cd3b815d1c7f140874c` |
| `qb-flatten` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.02431 | `run-0c7e302d52cc4b58af5049c3e492a82f` |
| `qb-gcd` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.020834 | `run-feba8e2a0d4a45f5bf3c71714ad56a79` |
| `qb-get_factors` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.025118 | `run-0b8c19e681f6484ab1d13a9753277e58` |
| `qb-hanoi` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-d167fe697875497b98935800b6fbbad4` |
| `qb-is_valid_parenthesization` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.022666 | 首次调用返回 &#96;&#96;&#96;json 围栏被判定 invalid_model_output，重试后通过；`run-24f7fd3625d04a73b1d0cf0de412b932` |
| `qb-kheapsort` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.032208 | 首次 model_transport_error、二次 detector 输出被 2048 token 上限截断；已将 profile max_output_tokens 提至 8192 后通过；`run-d6d7eca48ebd44c3a660df91fdce7ddf` |
| `qb-knapsack` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.029784 | 该题官方测试含 pytest.skip；`run-3397d515ca924ad88427916460c70ff3` |
| `qb-kth` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.026344 | `run-39289e9b42914dea83ade8746694b2c8` |
| `qb-lcs_length` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.037716 | `run-932c828920dc45299aaef61d098f1171` |
| `qb-levenshtein` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.037368 | 该题官方测试含 pytest.skip；`run-81c4846e3d844b829770f4040d6aecca` |
| `qb-lis` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.034752 | `run-a05d308d410f45deb04c3ca46cc057f5` |
| `qb-longest_common_subsequence` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.027562 | `run-6979c7499d924a1cb1eb36bf87383869` |
| `qb-max_sublist_sum` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.032638 | `run-d8597efa290d4911938e74802341f2c0` |
| `qb-mergesort` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.04897 | `run-ba90eb25590b4704bf8670250565dde3` |
| `qb-minimum_spanning_tree` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-38998b58a3a949428f91054198939219` |
| `qb-next_palindrome` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.033194 | `run-dc049861b724430aa06b54a36539a0cf` |
| `qb-next_permutation` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.024962 | `run-9cb18f6d2de54d23bf97f209b8e5a505` |
| `qb-pascal` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.030528 | `run-6a7d5a8fff784b2daee53bac67dd8489` |
| `qb-possible_change` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.028422 | 首次 repairer 输出多出 evidence_ids/knowledge_citations 字段被协议严格拒绝，重试后通过；`run-c245835d4aaa4f0e95cb9880cb326176` |
| `qb-powerset` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.019948 | `run-80d90f28c77e43bd8ff4c7d8dbc80882` |
| `qb-quicksort` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.019516 | `run-93308c11a8a9498a8cbf8c4d5ec9d6c2` |
| `qb-reverse_linked_list` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-02eab506bb2f4ae092ab43642032de3e` |
| `qb-rpn_eval` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.030348 | `run-d85bb292c86a4eb9bb49e1290c35cd77` |
| `qb-shortest_path_length` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-b53c43782829473cac4607f317f449ba` |
| `qb-shortest_path_lengths` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-19bc20d608a247b0bbc63d7cf41120f8` |
| `qb-shortest_paths` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-38f55bb5b3ad4486b8e8d24030867345` |
| `qb-shunting_yard` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-4f09497aa2b94358b9db612fe655d254` |
| `qb-sieve` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.02217 | `run-0a5e873655e548c8a1111f5d33c5109e` |
| `qb-sqrt` | 1 | 是 | passed | 无（基线 code_runtime_error，已修复） | 0.034422 | `run-445188104ced447a962420f31e454741` |
| `qb-subsequences` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.03781 | `run-6fbd3fd164a341a2837210e5644f0e5e` |
| `qb-to_base` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.020988 | `run-6cca594b1b9a4ba5ba48486575d2f4cf` |
| `qb-topological_ordering` | 0 | 否 | not_run | 预检阻断，未调用模型：Task has no approved pure JSON RPC adapter | 0 | `run-b73d82565184445cac5b6548fead9e51` |
| `qb-wrap` | 1 | 是 | passed | 无（基线 assertion_failed，已修复） | 0.057004 | `run-b206166f41c54b43952a63fe922406b4` |

> 备注：`qb-knapsack`、`qb-levenshtein` 的官方原生测试含 `pytest.skip`（慢测/长测），
> 已在生产 profile 登记精确 nodeid 作为 `allowed_skips`；其余 skip 仍会阻断。

## 汇总

| 项目 | 数值 |
|---|---|
| 题目总数 | 40 |
| `final_result = passed` | 29 |
| `final_result = not_run`（预检阻断，未调用模型） | 11 |
| 实际模型调用次数（计入表格的 29 次运行） | 87 |
| 表格内费用合计（CNY，估算） | 0.857644 |
| 含重试的实际调用次数 / 费用（CNY，估算） | 92 / 0.932368 |
| 全部运行 `status = success` | 29 |
| 全部运行 `status = blocked` | 11 |

### 通过率口径

- 29/40 = 72.5% 的任务在**真实模型 + Docker 隔离执行**下完成修复并通过原生测试。
- 11 个 `not_run` 并非模型失败，而是**生产适配器未登记**，在预检阶段阻断，`model_api_called = false`、费用为 0：
  `qb-breadth_first_search`、`qb-depth_first_search`、`qb-detect_cycle`、`qb-hanoi`、
  `qb-minimum_spanning_tree`、`qb-reverse_linked_list`、`qb-shortest_path_length`、
  `qb-shortest_path_lengths`、`qb-shortest_paths`、`qb-shunting_yard`、`qb-topological_ordering`。
- 若按"可执行题"口径统计，则为 **29/29 = 100%**。两种口径都在此列明，避免误读。

### 本轮运行中暴露并处理的三个工程问题

1. **`max_output_tokens` 截断（已修）**
   `qb-kheapsort` 的 detector 输出在 2048 token 处被截断（其中 reasoning 占 1586），JSON 不闭合被判 `invalid_model_output`。
   已将 `configs/model_profiles.json` 中 `aliyun_maas_ds_v41_flash.max_output_tokens` 由 `2048` 提升为 `8192`，
   与账本已审计的 `billable_output_tokens = 8192` 对齐（计费上限口径不变，仅放开单次输出长度）。改后该题一次通过。

2. **`model_transport_error`（重试解决）**
   `qb-kheapsort` 首次调用为传输层 `ValueError`，`attempts = 1 / max_attempts = 1`（传输重试配置为 1 次），批量层重试后恢复。

3. **模型输出协议严格性（未改动，记录为已知脆弱点）**
   - `qb-is_valid_parenthesization` 首次把 JSON 包在 &#96;&#96;&#96;json 代码围栏里 → `invalid_model_output`，重试通过。
   - `qb-possible_change` 首次在 repair 输出里多带 `evidence_ids` / `knowledge_citations` 字段 → 被 `_fields()` 严格拒绝，重试通过。
   解析器当前**不接受**代码围栏、也**不接受**协议外多余字段；这是刻意的严格校验，本轮未放宽。
   若后续批量规模扩大，建议评估"仅剥离 &#96;&#96;&#96;json 围栏"这一最小归一化，其余严格性保持不变。

### 复核方式

- 每题原始记录：`runs/batch1/<project>.json`（`runs/` 已 gitignore，不入库）
- 完整调用链/容器记录：`runs/platform/<run_id>/`
- 表格中每行的 `run_id` 均可回溯到上述目录。
- 全量单元测试：`385 passed, 18 failed`，18 个失败项在本次配置改动**前后完全一致**
  （本机 DPAPI 在当前账户上下文不可用、C runner 制品缺失），与本次改动无关。
