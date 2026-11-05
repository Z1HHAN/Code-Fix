# 超时与运行中停止 · 真实 Docker 实测

运行编号：`RUNTIME-20261105-123210-de75691a`；引擎版本：29.9.2；镜像：`sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`。

## 场景结果

| 场景 | 判据 | 结论 |
| --- | --- | --- |
| A 超时（单容器，模块级死循环） | bounded_time=True, timed_out_flag=True, status_timeout=True, failure_class_timeout=True, cleanup_passed=True, container_really_ran=True, no_container_left=True, no_volume_left=True | 通过 |
| A2 超时（双容器，函数内死循环） | bounded_time=True, returned_status=failed, not_passed=True, cleanup_passed=True, container_really_ran=True, no_container_left=True, no_volume_left=True | 通过 |
| B 运行中停止（真实控制面中断） | final_status_interrupted=True, control_is_stop=True, container_was_running_at_stop=True, no_final_check_dispatch=True, no_phase_after_stop=True, no_container_left=True, no_volume_left=True, stopped_promptly=True | 通过 |

## A 超时（单容器，模块级死循环）

- 实际耗时 14.89s（超时设定 12.0s）；采到容器：codefix-3c0d5eac88f149c5817e2e01df56d8ae, codefix-dc4790010f814b9cad19e1ed0797b6eb
- runner 结论：status=`timeout` failure_class=`timeout` timed_out=True cleanup_passed=True
- 残留容器 0 个、残留卷 0 个

## A2 超时（双容器，函数内死循环）

- 实际耗时 20.38s（超时设定 60.0s）；采到容器：codefix-1d6debe6d3924e408475224f32b50cf4, codefix-36a54aca7a6e4c95b2c67cb8ba2cdf02, codefix-candidate-10ab6cff960a4485bf4df7a66d05f990
- runner 结论：status=`failed` failure_class=`code_runtime_error` timed_out=False cleanup_passed=True
- 残留容器 0 个、残留卷 0 个

## B 运行中停止（真实控制面中断）

- 中断时刻（+8.21s）运行中的容器：codefix-6ba46380d2de491a815c66731077ac69|Up Less than a second|512bc2a4e681, codefix-candidate-a57ad42f68004c3a84b436c9464b407b|Up 1 second|512bc2a4e681
- 中断到 workflow 返回：1.46s
- 最终状态：`interrupted`，control=`stop`
- 实际派发的测试阶段：development, development
- 模型角色调用序列：repairer
- 残留容器 0 个、残留卷 0 个

## 结论

三个场景全部通过：超时有界返回并强杀容器，运行中停止后容器与卷清理干净且无后续派发。
