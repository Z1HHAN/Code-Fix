# 竞赛策略对照（固定 vs 动态）真实证据包

本目录是 B 分支 `codex/contest-strategies-config-comparison` 本轮交付的原件归档。
所有 `runs/`、`baseline/`、`isolation/` 内容均为本机真实执行产物的原样复制，不是摘要引用。

## 1. 冻结点与环境

| 项目 | 值 |
| --- | --- |
| 代码提交 | `ebc7450973081fe95d88a777f49f26633aa5d851`（本轮功能冻结版本，尚未合入 main；main 为 `731dc00…`） |
| 分支 | `codex/contest-strategies-config-comparison` |
| 工作区 | 采集开始时 `git status --porcelain` 为空；本提交只新增证据/文档，不改冻结功能代码 |
| Docker 引擎 | Server `29.9.2`，OSType `linux`，Arch `x86_64`，Kernel `6.18.54.1-microsoft-standard-WSL2`，Engine ID `9a5e7540-074f-4549-9e6a-02f78d140e9a` |
| runner 镜像 | `sha256:512bc2a4e681d483b63e94a711be9bfe8c6b85dde1b8cd3c7ce6c1c6c634e97f`（created `2026-11-04T17:14:14.806743013Z`） |
| evidence 镜像 | `sha256:6cceb56a1ad064ab7f3e3c092c42bc1698802160b62bd0382a5e055cfa639c2a`（created `2026-11-04T16:24:08.258309924Z`） |
| 镜像内 Python | `3.14.10`（`docker run --rm --network=none --entrypoint python <runner> -V` 实测） |
| 宿主机 Python | `3.13.0`（只用于启动本地工作台；候选执行一律在镜像内） |

两个镜像 ID 均先用 `docker image inspect` 逐字核对后才开始采集，未使用其他镜像。

## 2. 模型配置与资源上限

四角色（`locator` / `repairer` / `detector` / `scheduler`）统一绑定档案 `aliyun_maas_ds_v41_flash`：

- provider `aliyun_maas`，model `deepseek-v4.1-flash-1017`
- 价格版本 `operator-delegated-estimate-2026-11-06;aliyun-bailian-ds-class-peak`（高峰 input 2 CNY/M、output 8 CNY/M）
- `max_input_tokens=16000`、`max_output_tokens=8192`、`billable_output_tokens=8192`
- 任务参数：`max_rounds=3`、`max_calls=12`、`total_seconds=300`
- 工程上限沿用 `configs/runtime_limits.json`（`max_calls=30`、`max_tool_calls=5`、`total_seconds=1800`），本任务参数均在范围内

两份配置除 `strategy` 外逐字段相同（已用规范化比较验证）：

| 配置 | config_id | 策略 |
| --- | --- | --- |
| 动态 | `cfg-667da81832b592fb4f808b87e2c33c69b7bf5374c3d37ed9ad9e7e5f1419a671` | `dynamic` |
| 固定 | `cfg-6a8269e35bd95a18107ad4d7d3d9a3152ac7d70fc1215a682593b89a7ab136a2` | `fixed` |

校验结果：`roles_equal=true`、`prompts_equal=true`、`profiles_equal=true`、除策略外 `identical_except_strategy=true`。
每个运行的 `trace/experiment.json` 冻结身份由服务端计算；三组配对均 `identity_sha256` 相等（见 `comparison/comparison.json`）。

凭据处理：阿里云 key 保存在本机 DPAPI 文件中，仅以进程环境变量 `CODE_FIX_ALIYUN_MAAS_KEY` 注入服务进程；沙箱内进程无法解密（CryptUnprotectData 错误 2），因此在沙箱外以真实用户身份加载。证据包内不含密钥、`Authorization` 头或 key 指纹（全量文本扫描 `sk-` 命中 0）。

## 3. 隔离专项（无模型调用）

`isolation/docker-repair-132b0464623c/` 是 `scripts/check_repair_docker.py` 在冻结点上的完整输出：

- `status=passed`，`exit=0`，`model_api_called=false`，`isolation_ready=true`，`git_commit=ebc7450…`
- 关键控制项：`NetworkMode=none`、`ReadonlyRootfs=true`、`CapDrop=["ALL"]`、`SecurityOpt=["no-new-privileges"]`、`Memory=268435456`、`NanoCpus=1000000000`、`PidsLimit=64`、`Ulimits=nofile 128:128`、`Tmpfs=/tmp:rw,noexec,nosuid,size=67108864`

5 个用例与预期一致：

| 用例 | 结果 | 含义 |
| --- | --- | --- |
| normal | passed | 正常用例可执行 |
| fake_stdout | failed | 伪造 `passed` 输出被拦截 |
| audit_overflow | inconclusive | 审计日志溢出被拦截 |
| early_violation_then_overflow | inconclusive | 先违规后溢出仍被判定 |
| delayed_report_write | failed | 延迟写报告被拦截 |

说明：`runs/contest-qualification/` 下另有一次同脚本同提交的运行（`docker-repair-a24306b75f37`），结果同为 passed；本包以 `132b0464623c` 为权威复跑记录，未重复纳入以免歧义。

## 4. 原生基线（qb-get_factors，无模型调用）

`baseline/run-ec65ae064ecb4a889236b3891bd99112/`：

- `status=stopped`、`final_result=not_run`、`dev_result=failed`、`model_api_called=false`、`model_calls=0`
- 计数：collected 11 / passed 1 / failed 10 / errors 0 / skipped 0，`failure_class=assertion_failed`，`exit_code=1`
- 版本绑定：code `sha256:84c68b2a…`、test suite `sha256:66eda806…`
- 典型失败：`get_factors(63) -> [3, 3]`（期望 `[3, 3, 7]`）、`get_factors(74) -> [2]`（期望 `[2, 37]`）

这是登记的 `return []` 缺陷真实复现，不是环境错误。

## 5. 真实动态 / 固定配对（6 次运行，各只跑一次）

| 题目 | 策略 | run_id | 状态 | final | 轮次 | 模型调用 | 工具调用 | 费用(CNY) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qb-get_factors | dynamic | `run-fc80fc68ed934a4d9b7de6d0d36f9b91` | success | passed | 1 | 3 | 3 | 0.020810 |
| qb-get_factors | fixed | `run-24ee936a26144ebdabc74d62f2d256c1` | success | passed | 1 | 3 | 3 | 0.023660 |
| qb-gcd | dynamic | `run-0847d6ab11644e69aeb21d23d1e68871` | success | passed | 1 | 3 | 3 | 0.028106 |
| qb-gcd | fixed | `run-2027f3554be444788651e7a96a64d2fd` | success | passed | 1 | 3 | 3 | 0.023506 |
| qb-max_sublist_sum | dynamic | `run-e9993ae6676543149fd36e08a8bc89f2` | success | passed | 1 | 3 | 3 | 0.025864 |
| qb-max_sublist_sum | fixed | `run-0ec9da90642c4c5784da4734b8f69270` | success | passed | 1 | 3 | 3 | 0.026082 |

每个运行目录内含：原始调用 `calls/<call_id>/{input,output}.json`、`trace/`（含 `experiment.json` 冻结身份、`configuration.json`、逐步 `steps/` 与 `decisions/`）、`development-1/` 与 `final-check/` 的容器 `container.json`、`stdout/stderr`、JUnit、`pytest_events.json`、`cleanup.json`，`patch-1/patch.diff`、`final-work/`（干净副本）；运行级结果与事件以 API 导出的 `report-from-api.json`、`events-from-api.json` 固化（平台运行目录本身不落 `result.json`），另附 `bundle-<run_id>.zip`。

首题核对：`qb-get_factors` 开发测试 11/11 通过，干净 `final-check` 同套原生测试 11/11 通过，`evidence_matches_current_version=true`，补丁为把 `return []` 改为 `return [n]`（并改为 `while i*i<=n` 循环）。

本轮 6 次运行全部首次成功，**没有失败、阻塞或中途停止的运行，因此不存在重跑挑选**。首跑结果即上表。

## 6. 实验对照与导出

- `comparison_id`：`cmp-d09069c8a415e694f921b145c9c00daf039df7c34698d9e9baf3d1003a7008ff`
- scope：`formal`（正式计划，仅含已批准三题：`qb-get_factors`、`qb-gcd`、`qb-max_sublist_sum`）
- 汇总：两组均 planned 3 / ran 3 / executed 3 / success 3 / failed 0 / blocked 0 / not_run 0 / 全计划成功率 100%
- 费用合计：固定 `0.073248 CNY`，动态 `0.074780 CNY`（全部为 estimated，未知费用不计零）
- 导出：`comparison/comparison.json`、`comparison/comparison.csv`（服务端导出）、`comparison/comparison.png`（页面 canvas 导出，headless Edge 驱动真实 UI 的「导出 PNG 图表」路径生成，1200×720）
- 本对照没有注入场景；`runs/` 中的历史离线/注入运行未进入正式清单

## 7. 费用账本

沿用原账本 `runs/platform/model-ledger.sqlite3`，**未新建账本**。本轮开始前 `aliyun_maas` 已用 `0.950966 CNY`；本轮 6 次运行合计 `0.148028 CNY`（每次 3 次调用 × 6 = 18 次真实请求，`cny_calls` 全部 `status=known`）。结束后 `estimated_spent_cny=1.098994`、`unsettled_calls=0`，与 `ledger/budget-after.json` 一致：

- `authorized_cny=16`，`estimated_spent_cny=1.098994`，`reserved_cny=0`，`unsettled_calls=0`
- `estimated_remaining_cny=14.901006`，`remaining_after_reservations_cny=14.901006`

账本按 run 逐笔可复核，`cny_calls` 汇总、运行报告 `cost.amount_cny` 与预算快照三者一致：

| 执行顺序 | run_id | 本次 amount_cny | 运行后账本 estimated_spent_cny |
| --- | --- | ---: | ---: |
| 1 | `run-fc80fc68ed934a4d9b7de6d0d36f9b91` | 0.020810 | 0.971776 |
| 2 | `run-24ee936a26144ebdabc74d62f2d256c1` | 0.023660 | 0.995436 |
| 3 | `run-0847d6ab11644e69aeb21d23d1e68871` | 0.028106 | 1.023542 |
| 4 | `run-2027f3554be444788651e7a96a64d2fd` | 0.023506 | 1.047048 |
| 5 | `run-e9993ae6676543149fd36e08a8bc89f2` | 0.025864 | 1.072912 |
| 6 | `run-0ec9da90642c4c5784da4734b8f69270` | 0.026082 | 1.098994 |

每一步的增量均等于对应运行的 `cost.amount_cny`，且 `0.950966 + 0.148028 = 1.098994`，无缺口、无未结预留。注意 `budget-after.json` 是项目账本估算（`basis=project_ledger_estimate_not_platform_balance`），不等于阿里云控制台真实余额。

另外一次隔离专项与一次基线运行均 `model_api_called=false`，不计费。

## 8. 未运行 / 未覆盖 / 限制

- 未运行题目：无（正式清单三题都已跑完配对）。
- 资格说明：`qb-gcd`、`qb-max_sublist_sum` 在项目清单中的 `qualification_status` 仍为 `pending_docker_collection_and_baseline`，`admission_blocker=null`。本轮通过实际 repair 运行内的隔离探针与真实基线测试完成了等价采集，但**没有**回写项目清单字段；如需正式解锁，应由 A/C 决定是否把本轮证据登记为资格基线。
- 11 道此前被阻断的 QuixBugs 题仍未补适配器，不在本轮范围。
- 结论外推限制：本轮 6 次运行全部 1 轮通过，样本量小且三题难度低，**不能**据此宣称两种策略在成功率上等价；仅能说明「在本轮条件下两者均达到 100%，动态策略多花 0.001532 CNY、多耗时约 11 秒」。
- 隔离强度：Docker 探测为 Linux 容器级隔离，非 VM 等价认证（脚本自身 `scope` 字段已注明）。

## 9. 校验

- `manifest.json`：机器可读交付清单（代码提交、分支、镜像 ID、配置 ID、价格版本、资源上限、6 个 run 与费用、隔离/基线/未运行项）。
- `SHA256SUMS.txt`：本目录下全部文件的 SHA256（相对路径），`SHA256SUMS.txt.sha256` 为其自身校验值。
- 复核命令（在仓库根执行）：
  - `powershell -Command "Get-FileHash -Algorithm SHA256 <file>"`
  - 或 `sha256sum -c SHA256SUMS.txt`（Git Bash / WSL）
