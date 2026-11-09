# C 离线验收结果

运行编号：`C-acceptance-20261109-191654-9f4ea932`。退出码：0。

结论：passed_offline_scope。原平台回归与返修门槛分别登记；旧平台测试通过不能替代返修通过。

| 检查 | 状态 | 退出码 | 原因 |
| --- | --- | --- | --- |
| doctor | passed | 0 | 见原始记录 |
| validate_config | passed | 0 | 见原始记录 |
| platform_pytest | passed | 0 | 见原始记录 |
| node_ui | passed | 0 | 见原始记录 |
| c_controls | passed | 0 | 见原始记录 |
| repair_gates | passed | 0 | 见原始记录 |

平台pytest允许且单列宿主符号链接权限不足这一指定跳过项；返修门槛不允许skip/xfail代替通过。
完整记录、失败用例、计数和源码哈希见summary.json、各步step.json和JUnit。
未调用真实模型，未执行Docker/公开候选，没有真实final签收；不修改预算或隔离批准状态。
git_commit为空时仅有归档声明和文件哈希，不能登记为已检出该提交。
