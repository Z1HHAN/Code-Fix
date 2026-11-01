---
doc_id: business_rules
project_id: leap_demo
source_version: leap-fixture-v1
source_path: day1-kit/code/leap_year.py
---
# 闰年业务规则和接口约束

按公历规则，年份能被4整除，且不能被100整除或能被400整除时，是闰年。
公开任务接口为is_leap(year)，返回布尔值，不改变函数名称或参数。
本演示先处理任务约定的整数年份；未约定的输入行为不擅自扩张。
只有code/leap_year.py可被候选修改。测试文件和运行环境不能随修复修改。
业务规则可以进入角色上下文；参考实现、控制端密钥和评测端工作区不能进入。原生测试的开发反馈与独立复验分开记录。
