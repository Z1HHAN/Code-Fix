# C 的 Day 1 任务已完成

已完成环境准备、错误复现和参考答案验证。运行编号：`C-20261030-193201-b32afc2e`。

| 验证对象 | 通过 | 失败 | pytest 退出码 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 原始错误版本 | 3 | 1 | 1 | 仅 1900 年用例失败，复现成功 |
| 手册参考答案 | 4 | 0 | 0 | 相同测试全部通过，环境正常 |

这是 C 的环境与参考答案检查。参考代码直接来自手册，未调用 DeepSeek，不计作模型自动修复或 B 的完整闭环。

## 你现在怎么交付

把整个交付 ZIP 发给 B。两张图片在 `handoff/`；可复制的说明在 `handoff/给B的消息.txt`。本任务没有替你向队友发送消息。

- [错误版本测试截图](handoff/01_错误版测试.png)
- [参考答案测试截图](handoff/02_参考答案测试.png)
- [错误版本真实 ToolRun](runs/C-20261030-193201-b32afc2e/bad.toolrun.json)
- [参考答案真实 ToolRun](runs/C-20261030-193201-b32afc2e/reference.toolrun.json)
- [模拟接口样例](examples/SIMULATED_toolrun_failed.json)（明确标注模拟，供 B 编写读取逻辑）
- [完整运行清单](runs/C-20261030-193201-b32afc2e/manifest.json)（含命令、时间、哈希和校验结果）

两张 PNG 是原始 pytest 日志在本地 HTML 页面中的截图；原始 stdout、stderr、合并日志、JUnit 与代码副本全部保存在同一个 runs 目录，可相互核对。

## 这台电脑再跑一次

双击 `run_C.cmd`。它会生成新的 `runs/C-时间戳-随机值/`，运行错误版和参考答案两次测试，并自动记录 ToolRun。

或者在项目目录的 PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_c_checks.py
```

正确现象：先看到 `bad: 3 passed, 1 failed`，再看到 `reference: 4 passed, 0 failed`，最后 `C checks: passed`。第一份测试出现失败正是本次要求；脚本验收通过时整体退出码为 0。

每次运行的源文件都保留，测试仅在副本中执行。脚本设有 60 秒测试超时。截图对应本次固定 run_id，重新运行不会覆盖旧证据或自动更新截图。

## B 在另一台 Windows 电脑上复现

1. 解压 ZIP，保持目录结构。
2. 电脑需已有 Python 3.14；双击 `setup_C.cmd`，创建本机 `.venv` 并用 `wheelhouse/` 离线安装锁定版本。
3. 双击 `run_C.cmd`，核对两组结果。

ZIP 不包含本机的 `.venv` 和 Python 运行时，虚拟环境需要在 B 的电脑上重新建立。B 已有主项目时可复制 `code/`、`tests/`、`reference/` 与 `scripts/run_c_checks.py`，用自己的 Python 环境运行。

## 文件与接口约定

- `code/leap_year.py`：手册中的原始缺陷代码，故意保留缺陷，供 B 后续模型修复。
- `code/__init__.py`：新增的空包标记，使 `from code.leap_year import is_leap` 能正确定位项目模块。
- `tests/test_leap.py`：手册中的四条断言和测试函数保持一致。
- `reference/leap_year.py`：手册给定参考答案，只用于验证。
- `runs/<run_id>/work_bad/`、`work_reference/`：两次真实运行的独立副本。
- `*.toolrun.json`：严格使用 `call_id`、`tool`、`status`、`exit_code`、`counts`、`log_path`、`duration_ms` 七个字段。
- `log_path`：相对于交付包根目录；把目录整体解压后可以定位。
- `events.jsonl`：仅记录 C 检查事件，不冒充 B 的模型调用流水账。
- `examples/`：仅模拟接口样例；不要计入运行成绩。

当前环境是 Python 3.14.6、pytest 8.3.5，全部依赖见 `requirements-lock.txt`。为了避免同名标准库导入问题，使用 `python -m pytest`，并禁用外部 pytest 插件自动加载。

Python 运行时来源：未知（原始运行未记录来源元数据）。签名验证：未知（无签名验证证据）。版本以 manifest.json 为准。

本执行器仅用于固定示例的开发检查。普通子进程和 60 秒超时不提供模型代码安全隔离，也不保证清理完整进程树；不得用于自动执行不可信模型代码。

历史 manifest 中的 python_executable 和 command 保留原电脑路径用于审计，不是接收方启动路径；请使用 setup_C.cmd 与 run_C.cmd 建立本机环境。

交付校验：运行 `python scripts/verify_delivery.py`。重建材料后重新生成截图，再运行 `python scripts/verify_delivery.py --freeze` 生成索引与校验清单，最后再次校验。

任务依据：Day0 第 7 章 C 分工；Day1 第 4 章示例、第 5.2 节 C 任务卡、第 6.2 节 ToolRun、第 7.2 节副本与超时规则。今日交付未包含 Docker、模型 API 接入或整个团队的自动修复闭环。
