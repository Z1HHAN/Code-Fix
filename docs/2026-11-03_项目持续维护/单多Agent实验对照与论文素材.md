# 单Agent与多Agent实验对照：设置、结果与论文素材

本批计划40题×2组，已归档67/80条运行；完整配对33组。批次未完成，以下为部分结果。通过指原生隔离测试及干净副本最终复验通过；不代表任意输入下已证明程序正确。

执行代码冻结为 `5d720e325863622d3d7367321985ea2c79da1d2f`，运行源码SHA256为 `7312dd11218e81f2d052e494d9dc469b9370779fecb59b32ed3169284b7730b2`。应用对照记录 `cmp-dd56c8bc54afdc357293a98ae3bd011b36176c3f1b5fa1b9d817450c59a8e71b`。本文件、应用对照和图表读取同一比较快照；原始失败、暂停、请求及初始分段保留。

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
| 已有运行 | 33 | 34 |
| 已执行 | 33 | 34 |
| 最终复验通过 | 32 | 31 |
| 未运行 | 7 | 6 |
| 模型请求记录（含异常） | 39 | 86 |
| 全计划通过率 | 80.0% | 77.5% |
| 估算费用CNY | 0.115612 | 未知（已知小计0.254762） |

以下耗时为各任务开始至结束的合计，包含隔离/模型等待，不能当作纯模型延迟；整批墙钟时间含人工核销等待。

| 已完整配对指标 | 单Agent | 多Agent |
| --- | ---: | ---: |
| 完整配对题数 | 33 | 33 |
| 最终复验通过 | 32 | 31 |
| 有效候选轮次 | 39 | 37 |
| 模型请求记录 | 39 | 85 |
| 估算费用CNY | 0.115612 | 0.254762 |
| 任务耗时合计秒 | 1058.16 | 1268.89 |

配对结局（原始字段）：`{"both_success": 31, "first_only": 1, "second_only": 0, "neither_success": 1, "incomplete": 7}`。本批没有多次独立重复，不报告稳定优势或因果增益，不以机制实现/测试覆盖替代实验结论。

## 四Agent调用与Self-Loop实证

| 角色 | 单Agent请求记录 | 多Agent请求记录 |
| --- | ---: | ---: |
| repairer | 39 | 39 |
| detector | 0 | 37 |
| scheduler | 0 | 5 |
| locator | 0 | 5 |

- `qb-mergesort`：3轮，通过；实际角色调用 `{"scheduler": 2, "detector": 3, "repairer": 3, "locator": 2}`。原始报告：`evidence/single-multi-agent/111/run-5feb3a0bf6824b90a6a0ff634db6b488/report.json`。

- `qb-next_palindrome`：3轮，停止；实际角色调用 `{"locator": 2, "detector": 2, "scheduler": 2, "repairer": 3}`。原始报告：`evidence/single-multi-agent/111/run-ab4b130887334208bcc51dee6fd39262/report.json`。

- `qb-pascal`：2轮，通过；实际角色调用 `{"detector": 2, "locator": 1, "scheduler": 1, "repairer": 2}`。原始报告：`evidence/single-multi-agent/111/run-f98cdcffebb34d628c042bb68dff3fa1/report.json`。

这些是自然运行的交接与回流记录，不是故障注入。检测员解释/调度建议不是工具事实；RPC桥接栈等基础设施信息也可能进入错误证据，不能把模型推测写成已证实的根因。通过分支不强行调用所有角色；模型不能批准权限或覆盖测试结论。结构化交接与代码/测试版本证据见`observed-self-loops.json`及对应trace/calls原件。

## 费用异常、核销与续跑

初始15条运行因qb-flatten检测员ConnectError、无usage而停止。用户最终确认两张原始用量截图有效；其Token与已知请求汇总一致。通过现有人工核销接口将该条按0元处理，收据保留核销前unknown状态、理由与证据哈希。这是基于已确认汇总证据的操作员决定，不是供应商逐笔零扣费证明。原始调用usage仍为空；暂停任务未改成成功，也未在本批重试覆盖。

本批新增模型请求记录125条，新增已知估算0.370374元；累计授权台账已知估算2.181837元，未知/待结算1条。具体保留`manifest-segment-001.json`、`reconciliation-receipt.json`、`provider-usage-confirmed.json`，不将核销前后快照混写。

本次续跑在北京时间2026-11-15 23:33:22左右又出现一条新的未知费用：qb-shunting_yard修复员连接异常，调用m-92f654e0ab7d41d3a11c9e05f3740f72，无usage，剩余13条未启动。已请求用户核对本次账单；上一组截图和核销不适用于此条，当前不继续付费调用。最新pending-fee.json保留本条，旧核销状态归档pending-fee-resolved-001.json。

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
| qb-shunting_yard | 未运行 / 未记录 / 未记录 | 暂停 / 0 / 1 |
| qb-sieve | 未运行 / 未记录 / 未记录 | 未运行 / 未记录 / 未记录 |
| qb-sqrt | 未运行 / 未记录 / 未记录 | 未运行 / 未记录 / 未记录 |
| qb-subsequences | 未运行 / 未记录 / 未记录 | 未运行 / 未记录 / 未记录 |
| qb-to_base | 未运行 / 未记录 / 未记录 | 未运行 / 未记录 / 未记录 |
| qb-topological_ordering | 未运行 / 未记录 / 未记录 | 未运行 / 未记录 / 未记录 |
| qb-wrap | 未运行 / 未记录 / 未记录 | 未运行 / 未记录 / 未记录 |

逐题运行ID、停止原因、成本、基线/最终测试、报告路径见`per-task-results.csv/json`；失败与异常细节见`failure-review.json`。暂停/未最终通过不列为修复成功。当前完整配对中唯一单组通过的题为flatten，多Agent因连接异常暂停；next_palindrome两组均未通过，多组最后输出重复文件路径被拒绝。不能将这一网络导致的差异解释为算法优劣。

## 可用于论文和PPT的表述

> 在相同模型、冻结测试及资源限制下，我们对40道QuixBugs-Python程序设置单修复Agent基线和证据驱动多Agent协作组。本批已归档67条运行；单Agent通过32/40，多Agent通过31/40。我们同时报告真实请求、候选轮次、估算费用、耗时以及基础设施暂停。四角色分工与有界回流由实际调用和交接证据支持；本批结果不直接证明多Agent具有普遍性能优势。

- 可展示亮点：独立角色契约、双重交接校验、检测证据驱动的条件分支、有界Self-Loop、工具最终验收、预算与权限守卫、版本化可追溯结果。
- 不可写：DAG优化/学习能力画像、动态调度已证明提升9题、生产级大规模验证、所有分支每题都调用四Agent。
- 旧“25→34→40”来自不同开发阶段，未得到本批同条件消融验证；不能用于归因。
- 限制：单数据集、单模型、每条件每题一次；原生测试覆盖有限，网络/RPC与服务商状态影响真实运行。
- 应用记录及导出：`comparison.json/csv`；论文/PPT静态图`figures/`下PNG、SVG、PDF；真实页面`browser-real/`。`browser/`为离线排版夹具，不能作为真实实验结果。
- 当前为本地开发交付；C本人复跑签收、main合入与发布分别登记，不能由上述自动检查代签。
