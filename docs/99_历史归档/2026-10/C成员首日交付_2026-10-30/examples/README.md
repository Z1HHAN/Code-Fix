# 模拟接口样例

`SIMULATED_toolrun_failed.json` 是供 B 编写读取逻辑的模拟数据，字段严格遵循 Day1 第 6.2 节 ToolRun。

样例路径不存在，1420 毫秒是手册示例数字，均不是真实运行证据。为保持接口字段不变，模拟标记放在目录、文件名和本说明里。

真实的通过数、退出码、耗时和日志位置请查看 `runs/<run_id>/` 下的 `*.toolrun.json` 与 `manifest.json`。
