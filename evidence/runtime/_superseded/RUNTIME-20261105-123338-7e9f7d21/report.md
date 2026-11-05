# 超时与运行中停止 · 真实 Docker 实测

运行编号：`RUNTIME-20261105-123338-7e9f7d21`；引擎版本：29.9.2；镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`。

## 场景结果

| 场景 | 判据 | 结论 |
| --- | --- | --- |
| A 超时（单容器，模块级死循环） | bounded_time=True, timed_out_flag=True, status_timeout=True, failure_class_timeout=True, cleanup_passed=True, container_really_ran=True, no_container_left=True, no_volume_left=True | 通过 |
| A2 超时（双容器，函数内死循环） | bounded_time=True, returned_status=failed, not_passed=True, cleanup_passed=True, container_really_ran=True, no_container_left=True, no_volume_left=True | 通过 |
| B 运行中停止（真实控制面中断） | inflight_test_not_passed=True, inflight_test_killed=True, final_status_interrupted=True, control_is_stop=True, container_was_running_at_stop=True, no_final_check_dispatch=True, no_phase_after_stop=True, no_container_left=True, no_volume_left=True, stopped_promptly=True | 通过 |

## A 超时（单容器，模块级死循环）

- 实际耗时 14.87s（超时设定 12.0s）；采到容器：codefix-238ffa74978d43fd8b6bfb11f0f71df2, codefix-d536532f70ea48af89865b116e29a44a
- runner 结论：status=`timeout` failure_class=`timeout` timed_out=True cleanup_passed=True
- 残留容器 0 个、残留卷 0 个

## A2 超时（双容器，函数内死循环）

- 实际耗时 20.44s（超时设定 60.0s）；采到容器：codefix-candidate-a7dc93656c9f4b9bab1f0ad331266ff5, codefix-dc21a97aae5c4e3d9bd23208dd9f6674, codefix-fdab879ca2094f99a6e9d69a25d35101
- runner 结论：status=`failed` failure_class=`code_runtime_error` timed_out=False cleanup_passed=True
- 残留容器 0 个、残留卷 0 个

## B 运行中停止（真实控制面中断）

- 中断时刻（+9.88s）运行中的容器：codefix-7d25d4bb6ff642038fae91af71c0a4f3|Up 2 seconds|512bc2a4e681, codefix-candidate-d22152b398334125a61fe49dec47b791|Up 3 seconds|512bc2a4e681
- 中断到 workflow 返回：1.46s
- 最终状态：`interrupted`，control=`stop`
- 实际派发的测试阶段：development, development
- 模型角色调用序列：repairer
- 残留容器 0 个、残留卷 0 个

## 结论

三个场景全部通过：超时有界返回并强杀容器，运行中停止后容器与卷清理干净且无后续派发。
