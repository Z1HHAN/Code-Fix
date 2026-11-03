# C 已审阅的固定控制样例

当前状态核对2026-11-03：本目录是V3后端正在使用的可信夹具；目录名与登记哈希固定，不能随日期改名或移入归档。组件回归与真实模型候选验收分开，详见[C组件与历史证据状态](../docs/2026-11-03_项目持续维护/C测试组件与历史证据状态.md)。

本目录仅用于验证确定性 runner，不属于模型修复成绩，也不是 QuixBugs 目标测试。所有样例均无模型调用、无网络操作。`profiles.json` 冻结文件哈希、测试 ID 与源文件路径；runner 只能执行与冻结快照逐字节一致的副本。

| 样例 | 用途 | 预期 |
| --- | --- | --- |
| leap_bad | 原始闰年缺陷基线 | 3 通过、1 失败，1900 年断言失败 |
| leap_original | day1-kit Git原始字节，保留code路径 | 3通过1失败，供基线CLI与模拟联调 |
| leap_good | 已知正确参考 | 同一套四条测试全部通过，仅 reference_check |
| leap_all_skipped | 已知代码主动抛 SkipTest | 全部跳过，不得通过 |
| output_timeout | stdout/stderr 刷新后等待 30 秒 | 2 秒受控时限后 timeout，保留部分日志 |
| zero_tests | 测试文件没有测试函数 | 零用例，不得通过 |
| incomplete_tests | 实际只含 1 条，登记预期 4 条 | 缺测，不得通过 |
| collection_dependency_error | 导入刻意不存在的依赖 | 环境/收集错误，不得通过 |
| collection_syntax_error | 已知源文件语法错误 | syntax_error，不得误当测试断言失败 |
| code_runtime_error | 已知函数抛 ValueError | code_runtime_error，不得通过 |

闰年四条断言保留 main/day1-kit 的既有含义；这里使用 `leap_app` 导入避免标准库 `code` 同名问题。bad/good 测试文件字节一致。故障注入样例不用于推断模型能力。

使用 `scripts/check_c_controls.py --output-root runs/controls` 产生真实结果，使用 `python -m pytest c_tests` 验证控制边界。正确参考可供可信控制程序检查执行器，Agent上下文只接收leap_original坏代码，不扫描本目录或读取正确参考。
