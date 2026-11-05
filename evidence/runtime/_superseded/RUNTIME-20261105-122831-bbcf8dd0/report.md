# 超时与运行中停止 · 真实 Docker 实测

运行编号：`RUNTIME-20261105-122831-bbcf8dd0`；引擎版本：29.9.2；镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`。

## 场景结果

| 场景 | 判据 | 结论 |
| --- | --- | --- |
| A 超时（单容器，模块级死循环） | bounded_time=True, timed_out_flag=True, status_timeout=True, failure_class_timeout=True, cleanup_passed=True, container_really_ran=True, no_container_left=True, no_volume_left=True | 通过 |
| A2 超时（双容器，函数内死循环） | bounded_time=True, returned_status=failed, not_passed=True, cleanup_passed=True, container_really_ran=True, no_container_left=True, no_volume_left=True | 通过 |
| B 运行中停止（真实控制面中断） | final_status_interrupted=False, control_is_stop=False, container_was_running_at_stop=False, no_final_check_dispatch=True, no_phase_after_stop=True, no_container_left=True, no_volume_left=True, stopped_promptly=False | 不通过 |

## A 超时（单容器，模块级死循环）

- 实际耗时 15.07s（超时设定 12.0s）；采到容器：codefix-baaf9ea0924942e990260527ef16f656, codefix-da5c022df18041c39eb588a1f9f7fe7f
- runner 结论：status=`timeout` failure_class=`timeout` timed_out=True cleanup_passed=True
- 残留容器 0 个、残留卷 0 个

## A2 超时（双容器，函数内死循环）

- 实际耗时 20.52s（超时设定 60.0s）；采到容器：codefix-1159d240272048c4a7076c823282ca67, codefix-25c2ab72909749fdb1fee6a69cd41cf4, codefix-candidate-ef95305596ce46ce836847a93d266c0a
- runner 结论：status=`failed` failure_class=`code_runtime_error` timed_out=False cleanup_passed=True
- 残留容器 0 个、残留卷 0 个

## B 运行中停止（真实控制面中断）

- 中断时刻（+Nones）运行中的容器：无
- 中断到 workflow 返回：Nones
- 最终状态：`paused`，control=`None`
- 实际派发的测试阶段：development
- 模型角色调用序列：
- 残留容器 0 个、残留卷 0 个

## 结论

存在未通过场景，不得作为通过结论归档，需排查后再重跑。
