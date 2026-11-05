# 闰年真实模型闭环证据（leap-year）

本目录归档一次**真实模型调用 + Docker 隔离执行**的完整证据，不是模拟或离线替身。

## 运行信息

| 项 | 值 |
|---|---|
| run_id | `run-abfe208f4c784957b373eec2f74ad7e0` |
| 创建时间 | 2026-11-06 00:09:07 (+08:00) / 2026-11-05T16:09:07Z |
| 结束时间 | 2026-11-06 00:10:02 (+08:00) / 2026-11-05T16:10:02Z |
| 任务参数 | `{"project":"leap","mode":"repair","max_rounds":3,"max_calls":12,"total_seconds":600}` |
| 模型 | `deepseek-v4.1-flash-1017`（provider `aliyun_maas`） |
| 模式 | `mode=live`，`model_api_called=true` |
| 最终结果 | `final_result=passed`，`dev_result=passed`，`rounds_used=1` |
| 模型调用数 | 3（repairer / detector / scheduler），`tool_calls=3` |
| 估算费用 | 0.018598 CNY（项目账本估算，非平台真实余额） |

## 判定链路

1. 基线：原始坏版本在隔离容器里跑冻结测试 → `4` 条中 `1` 条失败（1900 被误判为闰年），退出码 1，预期失败。
2. 修复：repairer 返回严格 JSON，`changes` 只含 `code/leap_year.py`。
3. 开发验证：修复后 `4 passed`，`errors=0 failures=0 skipped=0`，退出码 0。
4. 终检：在**干净起点**重新应用候选，用同一套冻结测试（`test_suite_version=sha256:a1dcc0ff…`）复跑 → `4 passed`，退出码 0。
5. 越权审计：三次 `candidate_audit` 的 `violation` 均为 `null`，`fake_report_output=false`；`tests/test_leap.py` 在 base／candidate／final 三处哈希一致（`aa3a6c09…`），与登记的 `frozen_file_sha256` 相同。

## 文件清单

| 文件 | 说明 |
|---|---|
| `report.json` | 运行完整报告（步骤、决策、测试、候选审计、费用） |
| `junit-baseline.xml` | 基线（未修复）junit，`failures=1` |
| `junit-development.xml` | 开发验证 junit，`failures=0 errors=0` |
| `junit-final.xml` | 干净副本终检 junit，`failures=0 errors=0` |
| `final-patch.diff` | 由 harness 依据候选 `changes` 生成的实际补丁 |
| `final-candidate.json` | 候选元数据：base/candidate 版本哈希、冻结测试哈希、变更清单 |
| `ledger.json` | 本 run 的人民币计费流水与账号额度（估算口径） |
| `SHA256SUMS.txt` | 本目录全部归档文件（不含自身）的 SHA256 |

## 脱敏与有意排除

- `report.json` 中的阿里云工作空间主机名前缀 `ws-…` 已替换为 `REDACTED-WORKSPACE`，共 3 处，**其余内容逐字节保持不变**。原始 `report.json` 的 SHA256 为 `4b2428cd420955d6cb19c302c603c3a6c6061f54d5fc1f25d2ae64fc00cb65c5`，可用于比对。
- **未复制** `runs/platform/model-ledger.sqlite3` 原文件：其中 `account_keys` 表保存 API key 指纹。这里只导出本 run 的 `cny_calls` 与 `account_limits`，不含任何指纹。
- **未复制** `calls/*/input.json`、`calls/*/output.json`：内含完整角色提示词与模型原文，不属于本次交付范围。
- 未包含任何 API key、`Authorization` 头、密钥环境变量值或 `secrets/` 内容。
- junit 中的 `hostname` 是 Docker 容器 ID，运行结束后容器已删除，不构成环境标识泄露。

## 校验方法

```powershell
cd evidence/real-run/leap-year
Get-Content SHA256SUMS.txt | ForEach-Object {
  $h, $f = $_ -split '  ', 2
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower()
  if ($actual -ne $h) { "MISMATCH $f" } else { "OK $f" }
}
```

## 复现方式（会产生真实费用）

```text
POST /api/v3/tasks   {"description":"修复闰年判断","project":"leap","mode":"repair","max_rounds":3,"max_calls":12,"total_seconds":600}
POST /api/v3/tasks/{run_id}/start
```

需要已配置 `CODE_FIX_LIVE_APPROVED=1`、`CODE_FIX_ALIYUN_APPROVED=1`、单价变量、本机凭证 `CODE_FIX_ALIYUN_MAAS_KEY`，以及用户级 `CODE_FIX_ALIYUN_MAAS_BASE_URL`。
