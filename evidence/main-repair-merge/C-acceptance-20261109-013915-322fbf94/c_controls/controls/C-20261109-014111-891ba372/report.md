# C 受控执行器检查

运行编号：`C-20261109-014111-891ba372`。

这些是真实工具/参考/故障注入检查；未调用模型，不计模型修复成绩。接口仍待 B/C 确认，隔离未验证。

| 检查 | 工具状态 | 验证结论 | 分类 | 本项控制检查 |
| --- | --- | --- | --- | --- |
| leap_bad | blocked | not_run | environment_error | 未通过 |
| leap_good | blocked | not_run | environment_error | 未通过 |
| output_timeout | blocked | not_run | environment_error | 未通过 |
| all_skipped | blocked | not_run | environment_error | 未通过 |
| zero_tests | blocked | not_run | environment_error | 未通过 |
| incomplete_tests | blocked | not_run | environment_error | 未通过 |
| dependency_error | blocked | not_run | environment_error | 未通过 |
| syntax_error | blocked | not_run | environment_error | 未通过 |
| runtime_error | blocked | not_run | environment_error | 未通过 |
| stale_version | blocked | not_run | permission_violation | 通过 |
| modified_tests | blocked | not_run | permission_violation | 通过 |
| model_candidate_blocked | blocked | not_run | permission_violation | 通过 |

原始日志、JUnit、结构化记录及清理信息见各项 record.json 指向的 artifacts 路径。
source_commit 为空，仅用源码文件哈希绑定本次实现；不得补写不存在的 Git 提交。
