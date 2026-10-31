# Day 1 闰年修复闭环

本项目保留了一个故意写错的闰年函数，用于验证模型能否在副本中完成真实修复。源文件 `leap_app/leap_year.py` 应保持错误版本：运行项目根目录的测试，预期为 3 passed、1 failed。`run_task.py` 会创建 `runs/<run_id>/work/` 副本，只修改副本中的 `leap_app/leap_year.py`。

## 运行

在项目根目录的 PowerShell 中执行：

```powershell
python -m pip install -r requirements.txt
$env:OPENAI_API_KEY="你的密钥"
$env:OPENAI_MODEL="服务商提供的模型名"
$env:OPENAI_BASE_URL="服务商提供的 Base URL"
python test_api.py
python run_task.py
```

使用 OpenAI 官方接口时，可以省略 `OPENAI_BASE_URL`。不要把密钥写入源码或提交到 Git。

## 验收

`runs/<run_id>/` 应包含：

- `test_before.log`、`toolrun_before.json`：修复前真实测试结果；
- `model_response_round_1.txt`：模型原始回复；
- `test_after_round_1.log`、`toolrun_after_round_1.json`：修复后真实测试结果；
- `events.jsonl`：按时间记录测试、模型调用、补丁和最终结果；
- `work/`：本次运行的代码和测试副本。

模型若未一次修好，脚本最多尝试 3 轮；测试超时为 60 秒。`task_spec.json` 使用手册第 6 章的 TaskSpec 字段。`SIMULATED_tool_run_sample.json` 只是接口样例，不是运行证据。

## 已有真实运行

`runs/20261031-093357/` 保存了此前真实模型调用：修复前 3 passed、1 failed，模型回复后 4 passed、0 failed，`events.jsonl` 最终结果为 success。历史运行证据的 `log_path` 已整理为项目相对路径；后续新运行也会写入项目相对路径。
