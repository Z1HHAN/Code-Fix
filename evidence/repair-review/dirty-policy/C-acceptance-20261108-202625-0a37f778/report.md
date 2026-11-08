# C 离线验收结果（开发态：不等于干净commit验收）

运行编号：`C-acceptance-20261108-202625-0a37f778`。退出码：2。

结论：blocked_or_incomplete。原平台回归与返修门槛分别登记；旧平台测试通过不能替代返修通过。

| 检查 | 状态 | 退出码 | 原因 |
| --- | --- | --- | --- |
| doctor | blocked | None | Dirty worktree; commit first or explicitly use --allow-dirty for development checks |
| validate_config | blocked | None | Dirty worktree; commit first or explicitly use --allow-dirty for development checks |
| platform_pytest | blocked | None | Dirty worktree; commit first or explicitly use --allow-dirty for development checks |
| node_ui | blocked | None | Dirty worktree; commit first or explicitly use --allow-dirty for development checks |
| c_controls | blocked | None | Dirty worktree; commit first or explicitly use --allow-dirty for development checks |
| repair_gates | blocked | None | Dirty worktree; commit first or explicitly use --allow-dirty for development checks |

平台pytest允许且单列宿主符号链接权限不足这一指定跳过项；返修门槛不允许skip/xfail代替通过。
完整记录、失败用例、计数和源码哈希见summary.json、各步step.json和JUnit。
未调用真实模型，未执行Docker/公开候选，没有真实final签收；不修改预算或隔离批准状态。
git_commit为空时仅有归档声明和文件哈希，不能登记为已检出该提交。
