# 单Agent与多Agent实验对照：设置、结果与论文素材

本批计划40题×2组，已归档80/80条运行；完整配对40组。全部计划运行已结束。通过指原生隔离测试及干净副本最终复验通过；不代表任意输入下已证明程序正确。

执行代码冻结为 `5d720e325863622d3d7367321985ea2c79da1d2f`，运行源码SHA256为 `7312dd11218e81f2d052e494d9dc469b9370779fecb59b32ed3169284b7730b2`。应用对照记录 `cmp-e9de35fdb1220caf3f6eee7bfc1c200940f8065f5e31c9ac28064394dd90be63`。本文件、应用对照和图表读取同一比较快照；原始失败、暂停、请求及初始分段保留。

## 实验设置

| 项目 | 设置 |
| --- | --- |
| 数据集 | 全部40道已登记QuixBugs-Python；同题、同原始代码、同冻结原生测试 |
| 单Agent | 仅修复员；真实工具测试失败直接反馈修复员，在共同限制内重试 |
| 多Agent | 修复员、检测员、模型调度员、定位员独立角色契约；按检测证据与程序规则分支，定位要求双重交接；通过分支跳过非必需角色 |
| 共同保护 | 相同隔离、补丁权限、预算、停止条件、干净最终复验；最终结果不回流本轮Agent |
| 模型/服务 | deepseek-v4.1-flash-1017；maas.qianwenaiapi.com兼容接口，并非官方直连接口 |
| 生成设置 | 修复员最多8192输出Token，其余角色2048；enable_thinking=false；两组共享绑定与参数 |
| 上限 | 每任务3修复轮、12模型调用、300秒；跨任务累计5元授权 |
| 顺序 | 串行配对，逐题交替两组先后；每组每题保留本批首个运行，不挑最佳重跑 |
| 价格 | 冻结逐模型价格与实际返回usage估算；非供应商逐笔账单，未知不作零 |
| 历史边界 | 历史开发中累计修好40题与本批冻结对照分开；旧四角色版本不得追认成纯单Agent基线 |

## 全计划结果

| 指标 | 单Agent | 多Agent |
| --- | ---: | ---: |
| 计划题数 | 40 | 40 |
| 已有运行 | 40 | 40 |
| 已执行 | 40 | 40 |
| 最终复验通过 | 38 | 37 |
| 未运行 | 0 | 0 |
| 模型请求记录（含异常） | 49 | 98 |
| 全计划通过率 | 95.0% | 92.5% |
| 估算费用CNY | 0.147057 | 未知（已知小计0.286201） |

以下耗时为各任务开始至结束的合计，包含隔离/模型等待，不能当作纯模型延迟；整批墙钟时间含人工核销等待。

| 已完整配对指标 | 单Agent | 多Agent |
| --- | ---: | ---: |
| 完整配对题数 | 40 | 40 |
| 最终复验通过 | 38 | 37 |
| 有效候选轮次 | 48 | 43 |
| 模型请求记录 | 49 | 98 |
| 估算费用CNY | 0.147057 | None |
| 任务耗时合计秒 | 1366.43 | 1577.47 |

配对结局（原始字段）：`{"both_success": 36, "first_only": 2, "second_only": 1, "neither_success": 1, "incomplete": 0}`。本批没有多次独立重复，不报告稳定优势或因果增益，不以机制实现/测试覆盖替代实验结论。

## 四Agent调用与Self-Loop实证

| 角色 | 单Agent请求记录 | 多Agent请求记录 |
| --- | ---: | ---: |
| repairer | 49 | 45 |
| detector | 0 | 43 |
| scheduler | 0 | 5 |
| locator | 0 | 5 |

- `qb-mergesort`：3轮，通过；实际角色调用 `{"scheduler": 2, "detector": 3, "repairer": 3, "locator": 2}`。原始报告：`evidence/single-multi-agent/111/run-5feb3a0bf6824b90a6a0ff634db6b488/report.json`。

- `qb-next_palindrome`：3轮，停止；实际角色调用 `{"locator": 2, "detector": 2, "scheduler": 2, "repairer": 3}`。原始报告：`evidence/single-multi-agent/111/run-ab4b130887334208bcc51dee6fd39262/report.json`。

- `qb-pascal`：2轮，通过；实际角色调用 `{"detector": 2, "locator": 1, "scheduler": 1, "repairer": 2}`。原始报告：`evidence/single-multi-agent/111/run-f98cdcffebb34d628c042bb68dff3fa1/report.json`。

这些是自然运行的交接与回流记录，不是故障注入。检测员解释/调度建议不是工具事实；RPC桥接栈等基础设施信息也可能进入错误证据，不能把模型推测写成已证实的根因。通过分支不强行调用所有角色；模型不能批准权限或覆盖测试结论。结构化交接与代码/测试版本证据见`observed-self-loops.json`及对应trace/calls原件。

## 费用异常、核销与续跑

初始15条运行因qb-flatten检测员ConnectError、无usage而停止。用户最终确认两张原始用量截图有效；其Token与已知请求汇总一致。通过现有人工核销接口将该条按0元处理，收据保留核销前unknown状态、理由与证据哈希。这是基于已确认汇总证据的操作员决定，不是供应商逐笔零扣费证明。原始调用usage仍为空；暂停任务未改成成功，也未在本批重试覆盖。

本批新增模型请求记录147条，新增已知估算0.433258元；累计授权台账已知估算2.244721元，阻断未知/在途0条，保留预留待核对1条、上限0.084076元。含预留预算占用上限2.328797元。分段manifest、旧核销收据与用户截图原件保留，不将不同阶段混写。

第二分段在北京时间2026-11-15 23:33:22左右因qb-shunting_yard修复员ConnectError、无usage停止，留下13项未运行。2026-11-16用户批准改进后，按新保守预留政策显式转挂原0.084076元：actual仍为空、不是零扣费或账单核销，审计保留unknown原行和证据哈希。第三分段仅续跑13项，不覆盖失败和暂停；原编排/模型/测试源码冻结5d720e3不变，但外部费用异常处理政策发生扩展，论文需披露此协议修订，不能称预算政策从头到尾完全相同。详见evidence/transport-budget/112/historical-carry-receipt.json；新网络分类本轮另行用本机真实HTTPX与离线夹具验证，不能把本批旧编排任务当成新版付费验收。

## 逐题首试结果

| 题目 | 单Agent：结果/轮次/请求 | 多Agent：结果/轮次/请求 |
| --- | --- | --- |
| qb-bitcount | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-breadth_first_search | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-bucketsort | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-depth_first_search | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-detect_cycle | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-find_first_in_sorted | 通过 / 2 / 2 | 通过 / 1 / 2 |
| qb-find_in_sorted | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-flatten | 通过 / 1 / 1 | 暂停 / 1 / 2 |
| qb-gcd | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-get_factors | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-hanoi | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-is_valid_parenthesization | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-kheapsort | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-knapsack | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-kth | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-lcs_length | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-levenshtein | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-lis | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-longest_common_subsequence | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-max_sublist_sum | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-mergesort | 通过 / 3 / 3 | 通过 / 3 / 10 |
| qb-minimum_spanning_tree | 通过 / 2 / 2 | 通过 / 1 / 2 |
| qb-next_palindrome | 停止 / 3 / 3 | 停止 / 2 / 9 |
| qb-next_permutation | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-pascal | 通过 / 1 / 1 | 通过 / 2 / 6 |
| qb-possible_change | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-powerset | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-quicksort | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-reverse_linked_list | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-rpn_eval | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-shortest_path_length | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-shortest_path_lengths | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-shortest_paths | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-shunting_yard | 通过 / 1 / 1 | 暂停 / 0 / 1 |
| qb-sieve | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-sqrt | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-subsequences | 停止 / 2 / 3 | 通过 / 1 / 2 |
| qb-to_base | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-topological_ordering | 通过 / 1 / 1 | 通过 / 1 / 2 |
| qb-wrap | 通过 / 2 / 2 | 通过 / 1 / 2 |

逐题运行ID、停止原因、成本、基线/最终测试、报告路径见`per-task-results.csv/json`；失败与异常细节见`failure-review.json`。暂停/未最终通过不列为修复成功。flatten及shunting_yard多Agent因连接异常暂停；next_palindrome两组均未通过，多组最后输出重复文件路径被拒绝。不能将网络导致的差异解释为算法优劣；其他单组未通过以逐题原件为准。

## 可用于论文和PPT的表述

> 在相同模型、冻结测试及资源限制下，我们对40道QuixBugs-Python程序设置单修复Agent基线和证据驱动多Agent协作组。本批已归档80条运行；单Agent通过38/40，多Agent通过37/40。我们同时报告真实请求、候选轮次、估算费用、耗时以及基础设施暂停。四角色分工与有界回流由实际调用和交接证据支持；本批结果不直接证明多Agent具有普遍性能优势。

- 可展示亮点：独立角色契约、双重交接校验、检测证据驱动的条件分支、有界Self-Loop、工具最终验收、预算与权限守卫、版本化可追溯结果。
- 不可写：DAG优化/学习能力画像、动态调度已证明提升9题、生产级大规模验证、所有分支每题都调用四Agent。
- 旧“25→34→40”来自不同开发阶段，未得到本批同条件消融验证；不能用于归因。
- 限制：单数据集、单模型、每条件每题一次；原生测试覆盖有限，网络/RPC与服务商状态影响真实运行。
- 应用记录及导出：`comparison.json/csv`；论文/PPT静态图`figures/`下PNG、SVG、PDF；真实页面`browser-real/`。`browser/`为离线排版夹具，不能作为真实实验结果。
- 当前为本地开发交付；C本人复跑签收、main合入与发布分别登记，不能由上述自动检查代签。
