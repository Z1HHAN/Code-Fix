# 被取代的第4项运行记录（保留，不删除）

本目录下的运行都是真实执行过的，未被篡改；保留是为了让"发现缺陷→修正→重跑"的过程可追溯。
**权威结论以 `../RUNTIME-20261105-123528-c590ec08/` 为准。**

| 运行 | 时间 | 结果 | 为什么被取代 |
| --- | --- | --- | --- |
| `RUNTIME-20261105-122831-bbcf8dd0` | 2026-11-05 12:28 | **失败**（`operator-stop` 不通过） | 运行根目录放在 `evidence/` 深层路径，Docker Desktop/WSL2 的 bind mount 在容器内读 `/w` 返回 `EIO`，pytest 报 `Directory '/w' not found`，基线被判成非 `assertion_failed`，workflow 直接 `paused`。属真实环境缺陷，不是伪造或跳过。 |
| `RUNTIME-20261105-123210-de75691a` | 2026-11-05 12:32 | 通过 | 已改短临时目录；但中断发生在容器刚起来（`Up Less than a second`）时，对"跑到一半"说服力不足。 |
| `RUNTIME-20261105-123338-7e9f7d21` | 2026-11-05 12:33 | 通过 | 已满足"容器运行 2.5s 后再中断"；但死循环夹具仍内嵌在脚本字符串里，未落到 `tests/fixtures/runtime/`。 |

最终版运行 `RUNTIME-20261105-123528-c590ec08` 的候选源 SHA256 与
`tests/fixtures/runtime/hang_on_import.py`、`hang_in_call.py` 完全一致（记录在 `summary.json` 的
`candidate_sha256` 字段），证明夹具是从文件读取而非内嵌。
