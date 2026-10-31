---
doc_id: testing
project_id: leap_demo
source_version: leap-fixture-v1
source_path: day1-kit/configs/task.json
---
# 测试与运行说明

在任务副本根目录运行登记的开发命令python -m pytest tests/ -v。
模型不能自由选择其他命令。测试由执行器设置超时并收集退出码、计数、日志和代码版本。
4条公开开发用例只用于开发反馈，不代表全部输入已正确。
独立验收由评测端在干净副本应用补丁后执行，保留测试不进入Agent工作区或本轮修复反馈。
未运行、超时、跳过和结果未知须如实记录。修改后旧版本的通过记录失效。
