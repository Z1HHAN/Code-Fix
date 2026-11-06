# QuixBugs 批量闭环 · Batch 1

- 模型：`deepseek-v4.1-flash-1017`（provider = `aliyun_maas`）
- 端点：阿里云百炼 OpenAI 兼容模式（工作区子域已脱敏）
- 日期：2026-11-06
- 方式：真实模型 API 调用 + Docker 隔离环境执行测试
- 参数：`--max-rounds 3 --max-calls 12 --total-seconds 600`
- 费用口径：项目账本**估算值**（CNY），非平台实时余额
- 密钥：API key 仅在进程环境变量中读取，未写入本文件、日志或报告

## 结果表

| 题目名 | 跑了几轮 | 是否修复 | final_result | 失败原因 | 费用 | 备注 |
|---|---|---|---|---|---|---|

> 备注：`qb-knapsack`、`qb-levenshtein` 的官方原生测试含 `pytest.skip`（慢测/长测），
> 已在生产 profile 登记精确 nodeid 作为 `allowed_skips`；其余 skip 仍会阻断。

