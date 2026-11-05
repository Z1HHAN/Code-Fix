# C 受控执行器检查

运行编号：`C-20261105-194336-345b1c7d`。

这些是真实工具/参考/故障注入检查；未调用模型，不计模型修复成绩。接口仍待 B/C 确认，隔离未验证。

| 检查 | 工具状态 | 验证结论 | 分类 | 本项控制检查 |
| --- | --- | --- | --- | --- |
| leap_bad | failed | failed | assertion_failed | 通过 |
| leap_good | ok | passed | passed | 通过 |
| output_timeout | timeout | inconclusive | timeout | 通过 |
| all_skipped | failed | inconclusive | not_reproduced | 通过 |
| zero_tests | failed | inconclusive | not_reproduced | 通过 |
| incomplete_tests | failed | inconclusive | not_reproduced | 通过 |
| dependency_error | failed | inconclusive | environment_error | 通过 |
| syntax_error | failed | failed | syntax_error | 通过 |
| runtime_error | failed | failed | code_runtime_error | 通过 |
| stale_version | blocked | not_run | permission_violation | 通过 |
| modified_tests | blocked | not_run | permission_violation | 通过 |
| model_candidate_blocked | blocked | not_run | permission_violation | 通过 |

原始日志、JUnit、结构化记录及清理信息见各项 record.json 指向的 artifacts 路径。
source_commit 为空，仅用源码文件哈希绑定本次实现；不得补写不存在的 Git 提交。
