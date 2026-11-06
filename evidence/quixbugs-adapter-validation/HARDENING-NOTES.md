# QuixBugs RPC 适配器加固记录

日期：2026-11-06
范围：不调用模型的本地加固验证（零 API 费用）。真实模型闭环尚未启动。

## 1. 本轮修复的缺陷

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| 1 | `qb-flatten` 官方 correct 版本返回 generator，RPC 侧 `json.dumps` 无法序列化 | correct 代码被判失败，适配器误报 | `docker/candidate_rpc.py` 与 `app/split_runner.py` 同步声明 `MATERIALIZE_AS_LIST = {flatten, kheapsort}`，仅对登记函数按官方测试原生的 `list(...)` 语义物化 |
| 2 | `qb-kheapsort` 未登记 RPC 函数映射 | 该题无法进入适配器路径 | `app/split_runner.py` 增加 `qb-kheapsort -> kheapsort`，RPC 白名单同步 |
| 3 | 验证脚本把 `allowed_skips` 写入 `get_task()` 的独立对象，而 `SplitDockerRunner` 会重新加载任务 | `qb-knapsack` / `qb-levenshtein` 的第二轮验证仍走生产策略，被错误判失败 | 新增 `apply_in_memory_skips()`，写入 runner 自己的 task 并断言生效；补回归测试 |
| 4 | 反向对照判据要求 `passed == 0` | 坏版本通过个别用例即被误判为“未按预期失败”（flatten 1/7、kheapsort 1/4） | 改为“整体判定 failed + failure_class ∈ {assertion_failed, code_runtime_error} + failed > 0”；补回归测试 |
| 5 | 生产 profile 无精确 skip 登记，两题被 `admission_blocker` 阻断 | 40 题批量无法覆盖这两题 | 新增 `configs/tasks/native-skip-approvals.json` 作为唯一人工批准来源，importer 只登记精确 nodeid；两题移除 blocker |

## 2. 批次记录（失败证据保留，不覆盖）

| 批次 | 任务数 | 生产 profile 直接通过 | 反向对照 | 说明 |
|---|---:|---:|---|---|
| `batch-20261106-correct-adapters` | 28 | 25 | — | 旧批次：`qb-flatten` 序列化缺陷，`qb-knapsack`/`qb-levenshtein` skip 传递缺陷 |
| `batch-20261106-correct-adapters-final` | 29 | 29 | 判据过严，记为异常 | 任务全部通过；反向对照判据缺陷暴露 |
| `batch-20261106-correct-adapters-final-r2` | 29 | 29 | 2/2 按预期失败 | 判据修正后闸门通过 |
| `batch-20261106-correct-adapters-final-r3` | 29 | 29 | 2/2 按预期失败 | 最终批次：精确 skip 已登记，两题在生产 profile 直接通过，无进程内注入 |

## 3. 最终验收结果（r3）

- 上游固定提交：`4257f44b0ff1181dedaedee6a447e133219fcebf`（`git ls-tree` blob SHA + `git cat-file` 原始字节）
- Docker 镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`
- `task_count = 29`，`production_profile_passed = 29`，`adapter_contract_failed = 0`，`all_adapter_contracts_passed = true`
- `native_skip_in_memory_validated = 0`：两题无需进程内注入，skip 已写入生产 profile
- `negative_controls = 2`，`negative_controls_all_failed_as_expected = true`
  - `qb-flatten` 坏版本：`code_runtime_error`，1 passed / 6 failed
  - `qb-kheapsort` 坏版本：`assertion_failed`，1 passed / 3 failed
- `qb-knapsack`：9 passed / 1 skipped；`qb-levenshtein`：6 passed / 1 skipped
- `model_api_called = false`，`api_cost_cny = 0`

## 4. 原生 skip 的批准与落地

用户于 2026-11-06 批准以下两个精确 nodeid：

- `python_testcases/test_knapsack.py::test_knapsack[input_data9-13549094]`
- `python_testcases/test_levenshtein.py::test_levenshtein[input_data3-42]`

落地规则：

1. `configs/tasks/native-skip-approvals.json` 是唯一的人工批准来源，仓库内不得存在隐式放行。
2. 批准文件缺失时 importer 继续 fail-closed：静态扫描到慢测 skip 的题目仍写回 `admission_blocker`。
3. 批准文件存在时只登记文件内的精确 nodeid；nodeid 必须已存在于该题静态预登记的 `expected_nodeids`。
4. 未知题目、空条目、非字符串条目或不在预登记集合中的 nodeid 会直接 `ValueError`，且在任何写入之前失败。
5. 未登记的其他 skip 不在放行范围内，准入策略仍会阻断。
6. 40 题批量结果表中，这两题标注“该题官方测试含 pytest.skip”。

## 5. 复现命令

```powershell
$env:CODE_FIX_EXECUTION_APPROVED='1'
$env:CODE_FIX_DOCKER_IMAGE='sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f'
.venv\Scripts\python.exe scripts\validate_quixbugs_correct_adapters.py --batch <batch-name> --timeout 120
```

单元回归：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_review_round2.py tests/test_workflow_v3.py -q -p no:cacheprovider
# 116 passed
.venv\Scripts\python.exe -m pytest tests/test_quixbugs_import.py -q -p no:cacheprovider
# 18 passed
```

## 6. 环境说明

- 仓库 `.venv`：Python 3.13.0 + pytest 8.3.5 + fastapi，可运行 importer/工作流相关测试。
- `.venv` 全量套件结果：`385 passed, 18 failed, 1 skipped`。同一组 18 个失败已在 HEAD `73f5f2f` 的临时 worktree 中逐项复现（18 failed / 18 passed 集合完全一致），属既有环境/夹具限制（`c_runner` 要求 Python 3.14、DPAPI 用例、fake-runner 夹具），与本次改动无关。
- 系统全局 Python 3.13.0 + pytest 9.2.0：`app/quixbugs_import.py` 的静态 ID 逻辑硬性要求 pytest 8.3.5，会因 `IdMaker` 签名变化拒绝运行；这是环境版本限制，不是本轮改动失败。
- `c_runner` 硬性要求 Python 3.14，涉及 C runner 的测试需在 3.14 环境复跑。
- 本次验证未调用真实模型，证据中不含 API key、请求体或计费数据库。
- 运行结束后已检查：无残留 `codefix-` 容器、无残留 `codefix-rpc-` 临时卷。
