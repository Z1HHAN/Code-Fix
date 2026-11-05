# Docker 容器隔离资格证据包

## 证据状态与适用范围

本目录记录一次在 Windows + Docker Desktop（WSL2 Linux 后端）上的实际隔离验证。`02-runtime/`、`03-tests/` 与 `04-scan/` 中的数据均由本机 Docker、容器内命令和 Docker Scout 直接采集，未手工转录；模板占位内容已全部替换。最终采集时间为 UTC 2026-11-04T16:47:15.212Z（Asia/Shanghai 2026-11-05 00:47:15）。 隔离子项补充测试（tmpfs 持久化、Unix socket、资源限制）于 UTC 2026-11-04T18:08:18Z 完成，详见 9.7 节。

核心证明目标不是“容器正在运行”，而是证明：

1. 各项隔离配置已经在内核层面生效；
2. 越界访问会被系统明确阻止；
3. 其他成员可以按本文档独立复现全部结论。

## 目录

```text
docker-isolation-evidence/
├── README.md
├── SHA256SUMS
├── 01-config/
│   ├── Dockerfile
│   ├── compose.yaml
│   └── startup-params.txt
├── 02-runtime/
│   ├── docker-info.txt
│   ├── container-inspect.json
│   └── process-and-mounts.txt
├── 03-tests/
│   ├── permission-tests.txt
│   ├── tmpfs-persistence-tests.txt
│   ├── unix-socket-tests.txt
│   ├── resource-limit-tests.txt
│   ├── network-tests.txt
│   └── filesystem-tests.txt
└── 04-scan/
    ├── image-scan-report.json
    └── image-scan-raw.sarif.json
```

## 1. 测试环境和 Docker 版本

| 项目 | 记录值 |
|---|---|
| 操作系统 / 内核 | Windows 客户端 + Docker Desktop；容器内核 `6.18.54.1-microsoft-standard-WSL2` |
| Docker Client | 29.9.2，windows/amd64，context `desktop-linux` |
| Docker Server / Engine | Docker Desktop 4.97.0 (244584)，Engine 29.9.2，linux/amd64 |
| containerd / runc | containerd v2.4.2；runc v1.5.2；docker-init 0.19.0 |
| Cgroup 驱动和版本 | cgroupfs / v2 |
| 安全模块 | seccomp builtin（daemon）+ 容器内 `Seccomp=2`、`Seccomp_filters=2`；cgroupns |
| 主机名 | `docker-desktop` |
| 测试日期（时区） | 2026-11-05（Asia/Shanghai）；采集时间戳为 UTC |
| 执行人 | Codex automated collection（命令直采，无人工转录） |
| 复核人 | 待独立复核签字 |

采集主机必须记录 Docker Client 与 Server 的完整版本。若使用 rootless Docker，必须在此处注明，因为权限和网络边界与 rootful Docker 不同。

## 2. 被测镜像、容器名称及部署说明

| 项目 | 记录值 |
|---|---|
| 镜像构建文件 | `01-config/Dockerfile` |
| Compose 文件 | `01-config/compose.yaml`（采集时只加载该文件，无 runtime override） |
| Compose 项目 | `docker-isolation-evidence` |
| 测试服务 | `isolation-test` / 容器 `docker-isolation-test` |
| 对端服务 | `peer` / 容器 `docker-isolation-peer` |
| 镜像标签 | `docker-isolation-evidence:test-current` |
| 镜像 ID | `sha256:6cceb56a1ad064ab7f3e3c092c42bc1698802160b62bd0382a5e055cfa639c2a` |
| 主容器 ID | `f58704716eb1e0fab9c827e81b8bcfd5934d1119557ffaf8049de26db8ee0df0` |
| peer 容器 ID | `5ed8147df3651923728a61882124b3ee8328e177958d041456265862e4ddbfd4` |
| 网络 | `docker-isolation-evidence_isolation_net`，`internal: true`，无宿主机端口映射 |

部署命令：

```bash
cd docker-isolation-evidence
docker compose -f 01-config/compose.yaml build --pull
docker compose -f 01-config/compose.yaml up -d
docker compose -f 01-config/compose.yaml ps --all
```

销毁命令：

```bash
docker compose -f 01-config/compose.yaml down --volumes --remove-orphans
```

不得把密码、Token、私钥、云凭据、内部域名、真实内网地址或客户数据写入本包。发现后先删除并重新采集哈希；不要只在文档中打码而保留原始日志。

## 3. 证据采集命令

以下命令必须在已部署环境执行。将 `CONTAINER` 替换为 `docker-isolation-test`，将 `IMAGE` 替换为实际镜像引用。输出分别保存到 `02-runtime/`，并在文件首部记录 UTC 时间、执行人和命令版本。

```bash
docker version
docker info
docker image inspect IMAGE
docker inspect CONTAINER
docker ps --no-trunc
docker top CONTAINER
docker exec CONTAINER id
docker exec CONTAINER cat /proc/1/status
docker exec CONTAINER capsh --print
docker inspect CONTAINER --format '{{json .Mounts}}'
docker inspect CONTAINER --format '{{json .HostConfig}}'
```

建议的落盘方式：

```bash
CONTAINER=docker-isolation-test
IMAGE=docker-isolation-evidence:test-current
docker info > 02-runtime/docker-info.txt
docker inspect "$CONTAINER" > 02-runtime/container-inspect.json
{
  echo '# docker top'; docker top "$CONTAINER";
  echo '# /proc/1/mounts'; docker exec "$CONTAINER" cat /proc/1/mounts;
  echo '# ps aux'; docker exec "$CONTAINER" ps aux;
} > 02-runtime/process-and-mounts.txt
docker image inspect "$IMAGE"  # image ID 记录于 02-runtime/docker-info.txt
```

镜像扫描命令（本包实际使用 Docker Scout；组织批准的扫描器可替换，但必须保留工具版本、扫描时间和原始报告）：

```bash
docker scout cves --format sarif \
  --output 04-scan/image-scan-raw.sarif.json "local://$IMAGE"
sha256sum 04-scan/image-scan-raw.sarif.json
```

## 4. 隔离要求、测试方法和结果对比

| 隔离要求 | 测试文件 | 关键判据 | 实际结果 |
|---|---|---|---|
| 非 root、能力裁剪、禁止特权、seccomp | `03-tests/permission-tests.txt` | UID 非 0；`CapDrop=ALL`；`NoNewPrivs=1`；`Seccomp=2`、`Seccomp_filters=2`；挂载和模块加载失败 | **PASS** |
| 独立网络栈、同组通信与外网隔离 | `03-tests/network-tests.txt` | netns 与宿主参照不同；无发布端口；同组 peer 可达；外网不可达 | **PASS** |
| 根文件系统与挂载边界 | `03-tests/filesystem-tests.txt` | 根目录只读；宿主机敏感文件不可见；仅声明的 tmpfs 可写 | **PASS** |
| 镜像漏洞 | `04-scan/image-scan-report.json` | 扫描工具、版本、时间、摘要和原始报告摘要齐全 | **EXECUTED**：18 个漏洞（6 HIGH、10 MEDIUM、1 LOW、1 UNSPECIFIED），17 个无修复版本；不改变隔离判据结论 |
| tmpfs 跨实例持久化 | `03-tests/tmpfs-persistence-tests.txt` | 实例 1 写入成功后销毁容器；同配置新实例中 `test -e` 返回 1 | **PASS** |
| 宿主 Docker socket 隔离 | `03-tests/unix-socket-tests.txt` | `.Mounts` 无 docker.sock；`[ -S ]` 为 `SOCKET_ABSENT`；mountinfo 计数 0；读取失败 | **PASS** |
| CPU / 内存资源上限 | `03-tests/resource-limit-tests.txt` | `NanoCpus=500000000`、`Memory=268435456`、`MemorySwap=268435456`、`PidsLimit=128`；CPU 采样 5/8 落在 40%–60% 且无 >90%；内存压测 exit 137、`oom_kill` 0→1、`memory.peak`=268435456 | **PASS** |

每个测试文件都固定记录“测试目的、执行命令、预期结果、实际测试结果、结论”五部分。实际结果必须包含退出码、关键 stdout/stderr 片段、容器 ID、镜像 digest 和采集时间。

## 5. 复现和复核流程

第一名成员按以下顺序执行：

1. 在干净主机校验本包文件哈希；
2. 记录 Docker、内核、运行时和安全模块版本；
3. 按第 2 节构建并启动容器；
4. 按第 3 节采集运行时证据；
5. 逐项执行 `03-tests/` 中的命令，把原始输出写入对应文件；
6. 使用批准的扫描器扫描实际镜像，把 JSON 报告摘要写入 `04-scan/image-scan-report.json`；
7. 删除敏感信息后重新生成哈希；
8. 由另一名成员独立重跑关键测试，不读取第一名成员的“实际结果”后再对照；
9. 复核人记录差异、退出码和结论，并在本 README 签字。

第二名成员至少必须重跑：非 root 与 capability、挂载/模块加载失败、网络 namespace、对端连通性、未发布端口、只读根文件系统、宿主机敏感文件访问和哈希校验。任何一项结果不同，都应将结论改为 `inconclusive`，保留两次原始输出并调查差异。

## 6. 未覆盖风险和例外

- Docker daemon 本身、宿主机 root、Docker socket、编排平台控制面或拥有 `CAP_SYS_ADMIN` 的其他主体可能绕过容器边界；本包不证明宿主机已安全。
- `internal: true` 只限制该 Compose 网络的外部连通性，不等同于物理网络隔离，也不防止宿主机主动访问容器。
- 默认 seccomp、AppArmor/SELinux 配置依赖宿主机；必须把实际 profile 名称和审计日志纳入复核。
- 本包不证明内核无未公开漏洞、不证明镜像内应用无业务漏洞，也不替代容器运行时或主机基线审计。
- `docker exec`、调试工具、临时端口映射、bind mount、host network、host PID、privileged、额外 capability 任何一项变更，都要求重新采集全部相关证据。
- 终端输出中的 IP、主机名、路径、镜像仓库、内部域名、Token、用户名和证书必须按企业脱敏规则处理；脱敏不得改变用于判定的字段。

## 7. 结论及签字

技术判据结论：**PASS**。四项最终验证（peer running 且监听 8080、主容器到同组 peer 可达、主容器到外网不可达、seccomp 内核态 2/2）全部通过；permission 测试确认非 root、capability 全空、`no-new-privileges` 生效，filesystem 测试确认只读根层和挂载边界。镜像扫描已执行，但仍有 18 个已知漏洞，不能视为“无漏洞”。

归档状态：已完成真实数据采集并生成压缩包与 SHA-256。正式资格结论仍需独立复核人签字。

```text
技术结论：PASS
结论依据：02-runtime、03-tests、04-scan 与 SHA256SUMS

执行人：Codex automated collection  日期（UTC）：2026-11-04
复核人：________________            日期（UTC）：________________
批准人：________________            日期（UTC）：________________
```

## 8. 哈希校验和防篡改

`SHA256SUMS` 是证据文件的 SHA-256 清单。接收方在同一目录执行 `sha256sum -c SHA256SUMS`，可以发现文件内容或文件名变化。SHA-256 只能证明“校验时与清单一致”，不能替代签名；清单应通过受信渠道传递，生产环境建议再使用企业 GPG/KMS 签名。

证据目录内的 `SHA256SUMS` 默认列出目录内各证据文件（不含自身），避免把归档文件的哈希写进归档内部造成循环依赖。归档文件哈希应作为同级 sidecar 清单生成：

```bash
# 打包证据包
tar -czf docker-isolation-evidence-YYYY-MM-DD.tar.gz docker-isolation-evidence/

# 生成归档哈希校验文件（建议放在归档同级目录，不要再次放入已生成的归档）
sha256sum docker-isolation-evidence-YYYY-MM-DD.tar.gz > SHA256SUMS

# 接收方验证归档完整性
sha256sum -c SHA256SUMS
```

若需要校验目录内文件，进入目录执行：

```bash
cd docker-isolation-evidence
sha256sum -c SHA256SUMS
```

## 9. 平台适配说明

本节记录本证据包在 **Windows + Docker Desktop（WSL2 Linux 后端）** 上落地时与“通用 Linux 宿主机”假设不一致的平台差异，以及为完成可复现测试所做的配置修正。配置修正共两处：peer 的 `entrypoint: []` 测试夹具修正，以及移除非法的 `seccomp:default`、改为由 Engine 隐式施加默认 profile。两处均不削弱主容器隔离参数，主容器逐项核对见 `02-runtime/container-inspect.json` 与 `01-config/startup-params.txt`。

### 9.1 peer 服务 entrypoint 置空（测试夹具修正）

- **现象**：`01-config/Dockerfile` 声明 `ENTRYPOINT ["/bin/sh","-c"]`，而 `01-config/compose.yaml` 的 `peer` 服务用 `command:` 提供 `["/bin/sh","-c","while :; do ... nc -l -p 8080; done"]`。Compose 的 `command` 只替换镜像 `CMD`，**不替换** `ENTRYPOINT`，因此实际 argv 为 `/bin/sh -c /bin/sh -c "while :; do ... nc -l -p 8080; done"`。
- **后果**：按 POSIX `sh -c` 语义，`-c` 后面的字符串是 `/bin/sh`，其后的 `-c` 成为 `$0`、循环体成为 `$1`；该子 shell 既没有脚本文件也没有 stdin，读到 EOF 后立即以 0 退出。`restart: "no"` 下容器不会重启，`8080` 永不监听，网络正向对照失效。
- **修正**：仅在 `peer` 服务增加一行 `entrypoint: []`，使 `command` 直接成为容器 argv。`isolation-test` 的 entrypoint 与隔离参数未因本项修正而改变；seccomp 修正见 9.5。
- **验证**：`docker compose -f 01-config/compose.yaml config` 输出中 `peer.entrypoint` 为空数组、`isolation-test.entrypoint` 仍为 `null`（沿用镜像 `ENTRYPOINT`）。

### 9.2 为什么这不构成隔离削弱

- 改动只作用于 **peer 对照容器**。该容器不是被审计对象，职责仅是在隔离网络内提供一个可预期的 HTTP 响应端点。
- 被审计主容器 `isolation-test` 的 `user`、`privileged`、`read_only`、`init`、`cap_drop`、`security_opt`、`tmpfs`、`pids_limit`、`mem_limit`、`cpus`、`network`、`restart` 均与 `01-config/startup-params.txt` 一致，逐项核对见 `02-runtime/container-inspect.json`。
- `entrypoint: []` 不是安全选项：它不改变 capabilities、seccomp、AppArmor/SELinux、namespace 或挂载。peer 仍以 `10000:10000` 非 root、`cap_drop: ALL`、`read_only: true`、`no-new-privileges` 运行。
- 若不做该修正，peer 会启动即退出，网络测试将失去"同网络对端可达"的正向基线，只能证明"全部不可达"，无法区分"隔离生效"与"对端根本没起来"。

### 9.3 WSL2 后端

| 项目 | 实测值 |
|---|---|
| Docker Desktop | 4.97.0 |
| Docker Engine | 29.9.2 |
| 内核 | 6.18.54.1-microsoft-standard-WSL2 |
| OSType / Arch | linux / x86_64 |
| 存储驱动 | overlayfs（containerd snapshotter） |
| Cgroup | cgroupfs / v2 |
| 默认 runtime | runc（containerd `b7a25261e029d68978bbf290df0a62fc819b3c51`、runc `v1.5.2`、docker-init `de40ad0`） |
| 安全模块 | seccomp `profile=builtin`、`cgroupns` |

WSL2 下 Docker 运行在 `docker-desktop` 这个 WSL 发行版内，容器看到的是 WSL2 提供的 Microsoft 标准内核，而非 Windows NT 内核。因此本包的 namespace、capability、seccomp、cgroup 判据与原生 Linux 宿主机同构。但**不要**把 Windows 侧的 Hyper-V / Defender 策略、`%USERPROFILE%` 路径映射当作容器隔离证据。

另需注意：本环境中 `docker run --network host` 等价于加入 `docker-desktop` 发行版的网络命名空间，而不是 Windows 主机网络。本包正是据此构造 host-network 参照容器做 netns 比对（见 `03-tests/network-tests.txt`），该参照只用于证明"容器 netns ≠ 宿主 netns"，不代表参照容器的网络等价于物理宿主机。

### 9.4 Docker Desktop 内置代理

`docker info` 报告：

```text
HTTP Proxy: http.docker.internal:3128
HTTPS Proxy: http.docker.internal:3128
No Proxy: hubproxy.docker.internal
```

这是 **Docker Desktop 提供给 dockerd / buildkit 的镜像拉取代理**，用于让镜像拉取与构建走出 Windows 侧代理设置。它：

- 不是容器内环境变量，`docker exec` 进入容器看不到 `HTTP_PROXY` / `HTTPS_PROXY`；
- 不为容器提供外网通路，容器外联仍受 `internal: true` 网络阻断；
- **不能**作为"容器可访问外网"的证据，也**不能**反过来当作"容器被隔离"的证据。

判定容器外联能力必须使用**容器内**的独立 canary（本包使用容器内 `nc` 直连外部 IP，并分别记录 DNS 解析失败与连接超时的退出码），不得引用宿主 `docker info` 的代理字段作为结论依据。

### 9.5 seccomp 配置缺陷与修正（模板缺陷，非平台差异）

**初始配置**：`01-config/compose.yaml` 两个服务均声明

```yaml
security_opt:
  - no-new-privileges:true
  - seccomp:default
```

`seccomp:default` 不是 Docker 的合法取值。Compose 把 `seccomp:<值>` 统一解析为 seccomp profile **文件路径**，并相对 compose 文件所在目录解析，于是实际行为是去找 `01-config/default` 这个文件：

```text
service:peer:1 opening seccomp profile (default) failed:
open ...\docker-isolation-evidence\01-config\default: The system cannot find the file specified.
```

容器因此完全无法启动（`docker compose up -d` 退出码 1，且两个服务都失败）。Docker Engine 只认 `seccomp=unconfined` 与 `seccomp=<路径>`；"默认 profile"是**不写该选项**时由 Engine 隐式施加的，不存在 `default` 关键字。

**上一轮的错误处理**：2026-11-04 01:47 留下的 `.runtime-compose-override.yaml` / `.runtime-compose.yaml` 采用了 `seccomp=default` 写法或直接删掉该行，并在 `docker-info.txt` 中记为"Docker Desktop on Windows 拒绝了显式 seccomp=default，因此改用 daemon 默认内置 profile"。该归因不成立：

- 报错与 Windows / WSL2 无关，Linux 原生 Docker 同样会失败（本轮在 `docker run` 上用第 4 组实验直接复现，exit 125）；
- 上一轮结束后 `docker inspect` 的 `HostConfig.SecurityOpt` 只剩 `["no-new-privileges:true"]`，报告却引用 `docker info` 的全局 `Security Options: seccomp / Profile: builtin` 作为"容器已加载 seccomp"的依据。**daemon 支持 seccomp ≠ 该容器加载了 profile**，两者不能互相替代。

**最终修正**：两个服务均删除 `seccomp:default`，只保留 `no-new-privileges:true`，并加注释说明 seccomp 由 Engine 默认施加；`01-config/startup-params.txt` 同步更新。

**证据判据**：不再依赖 inspect 中的配置字符串（默认 profile 本来就不会出现在 `HostConfig.SecurityOpt` 中），改为读取容器内 `/proc/1/status` 的内核态字段。

四组基线（2026-11-05 实测，`docker run --rm alpine:3.20`）：

| 启动方式 | `HostConfig.SecurityOpt` | `Seccomp` | `Seccomp_filters` |
|---|---|---|---|
| 不指定 seccomp（Engine 默认，本包采用） | `null` | 2 | **2** |
| `--security-opt seccomp=unconfined` | `["seccomp=unconfined"]` | 2 | 1 |
| `--privileged` | — | 2 | 1 |
| `--security-opt seccomp=default` | — | 容器无法启动，exit 125 | — |

判据：被测容器必须同时满足 `HostConfig.SecurityOpt == ["no-new-privileges:true"]`、`Seccomp == 2`、`Seccomp_filters == 2`（与"Engine 默认"基线一致，且与 unconfined / privileged 的 1 明确区分）。实测结果见 `03-tests/permission-tests.txt`。

**平台附注**：本环境下即使 `seccomp=unconfined` 与 `--privileged` 也报告 `Seccomp: 2`，只是过滤器数量降为 1（推测来自 WSL2 / containerd 层的另一处 filter）。因此**单看 `Seccomp:` 字段不足以区分**，必须结合 `Seccomp_filters` 计数与 `HostConfig.SecurityOpt` 共同判定。

### 9.6 `internal: true` 网络判据修正

- **初始预期误设**：早期测试把“主容器访问 peer:8080 必须失败”作为隔离通过条件。
- **实测结果**：`internal: true` bridge 允许同组容器互通，主容器访问 peer 实际返回 `open` 与 HTTP `READY`；这是 Docker 网络模型的预期行为。`internal` 限制的是该网络到外部网络的路由，不是同组成员之间的通信。
- **最终判据**：以“**同组可达 + 外网不可达**”作为网络隔离通过标准，具体为：主容器 → 同组 peer 可达；主容器 → 外网 IP 和域名均不可达；两条同时成立才证明 `internal: true` 按设计工作。
- **本轮实测**：peer `Running=true`、监听 `:8080`；主容器到 `docker-isolation-peer:8080` 为 `open`，HTTP 返回 `READY`；主容器到 `1.1.1.1:443` 为 `Network unreachable`，`example.com` 解析失败；容器路由表无 default route。详见 `03-tests/network-tests.txt`。

该修正不涉及任何配置放宽；`01-config` 仍使用同一条 `internal: true` bridge。把“同组可达”误判为失败会破坏 peer 作为网络正向对照的意义，也无法区分“外网隔离生效”与“容器网络整体失效”。

#### 9.6.1 复核提示：peer 夹具是单连接监听

peer 的监听实现为 `while :; do printf 'HTTP/1.1 200 ...' | nc -l -p 8080; done`，即**每次只服务一个连接**，处理完立即退出并由循环重新拉起。因此：

- **正确复核方式**：直接发起 HTTP 请求，例如 `docker exec docker-isolation-test wget -qO- --timeout=5 http://docker-isolation-peer:8080`，应稳定返回 `READY`（本轮实测连发 3 次，3/3 返回 `READY`、`rc=0`）。
- **易误判的用法**：先用 `nc -zv` 探测端口、紧接着再发 HTTP 请求。`nc -zv` 本身就会占用掉那一个连接名额，若第二次连接恰好落在"旧 `nc` 已退出、循环尚未重新绑定"的毫秒级窗口内，会看到 `Connection refused`。这是夹具的单连接特性所致，**不是**网络不通或隔离异常。
- 判别方法：`docker exec docker-isolation-peer netstat -ltn` 若显示 `:::8080 LISTEN`，且单独发起的 HTTP 请求返回 `READY`，即判定 4b 通过；不要以"`nc` 探测之后的紧邻一次 HTTP 失败"作为隔离失败证据。

### 9.7 隔离子项补充测试（2026-11-05 追加）

本节记录在原有四项判据之外追加的三组固化测试。三组测试全部通过本机 Docker 真实执行，命令与原始输出分别见 `03-tests/tmpfs-persistence-tests.txt`、`03-tests/unix-socket-tests.txt`、`03-tests/resource-limit-tests.txt`。采集脚本为仓库内 `scripts/docker_isolation_extra_tests.ps1`，仅加载 `01-config/compose.yaml`，启动前校验证据目录内不存在任何 `.runtime*` override 文件，任一组判据不满足即非 0 退出、拒绝生成 PASS 结论。

| 项目 | 记录值 |
|---|---|
| 采集开始（UTC） | 2026-11-04T18:07:04.943Z |
| 采集结束（UTC） | 2026-11-04T18:08:18.233Z |
| 镜像 | `docker-isolation-evidence:test-current` / `sha256:6cceb56a1ad064ab7f3e3c092c42bc1698802160b62bd0382a5e055cfa639c2a` |
| 测试实例 1（写入后销毁） | `77c7b7c792f11f5e8fb8cfce5baaa3089f12730916720077fff6dcda50db44e5` |
| 测试实例 2（资源测试所在容器） | `ec884ad2afe06ea7f768071d5c461919af5ded3eeb5155aa6f5f81d45b3b514c` |
| 脚本退出码 | 0（`RESULT: PASS`） |

**1) tmpfs 跨实例持久化：PASS**

实例 1 内成功写入 `/tmp/persistence-probe.txt` 并回读；`docker rm -f` 销毁实例 1 后，用同一 `01-config/compose.yaml` 启动实例 2（容器 ID 不同）。实例 2 中 `ls` 返回 1、`test -e` 返回 1、`/tmp` 为空目录。可写目录只有 tmpfs，数据随容器销毁回收，不具备跨实例持久化能力。

**2) 宿主 Docker socket 隔离：PASS**

`docker inspect --format '{{json .Mounts}}'` 输出 `[]`；容器内 `/var/run/docker.sock` 不存在，`[ -S ]` 判定为 `SOCKET_ABSENT`；`/proc/1/mountinfo` 中 `docker.sock` 计数为 0；`cat /var/run/docker.sock` 以退出码 1 失败。主容器没有任何通往宿主 Docker daemon 的 Unix socket 通道。

**3) CPU / 内存资源上限：PASS**

`docker inspect` 实测 `NanoCpus=500000000`、`CpuQuota=0`、`CpuPeriod=0`、`Memory=268435456`、`MemorySwap=268435456`、`PidsLimit=128`，与 `01-config/compose.yaml` 的 `cpus: "0.50"`、`mem_limit: 256m`、`memswap_limit: 256m`、`pids_limit: 128` 一致（`NanoCpus` 与 `CpuQuota/CpuPeriod` 两种表达互斥，本环境由 `NanoCpus` 生效）。

- **CPU**：容器内 `timeout 10 yes >/dev/null` 制造单线程满载，宿主机侧 `docker stats --no-stream` 连续采样 8 次，`CPUPerc` 为 `50.22/50.07/50.27/50.18/50.71` 后回落到 `0.00/0.01/0.03`；落在 40%–60% 区间的样本 5 个，超过 90% 的样本 0 个。0.50 核配额生效，容器无法吃满一个物理核。压测由容器内 `timeout` 自限时，到时以 SIGTERM 结束（`docker exec` 退出码 143 = 128+15），不依赖宿主机 `pkill`，避免误伤 PID 1。
- **内存**：容器内持续倍增分配字符串直至触顶，`docker exec` 退出码 137（128+SIGKILL）；cgroup v2 `memory.events` 的 `oom`/`oom_kill` 计数由 0 增至 1，`memory.peak` 达到 268435456 字节，等于 `memory.max`；`docker stats` 采样到 `235.4MiB / 256MiB`（91.97%）后回落。被 OOM 杀死的是 `docker exec` 启动的压测进程，容器 PID 1 仍存活，因此 `State.Status=running` 而 `State.OOMKilled=true`；两者并用可区分"容器仍在服务"与"内存上限确实触发"。

**方法学说明**：早期版本曾用 `docker exec ... awk 'BEGIN{...}'` 直接压测，因 /bin/sh 中 `\r\n` 与 `$` 字符解析问题两次得到假的 `exit 0`，脚本已改为无嵌套引号的纯 shell 变量倍增，并以 cgroup v2 内核计数与 `State.OOMKilled` 双重校验；假 PASS 的中间产物已删除，不作为证据。CPU 侧早期版本用 `pkill -f` 清理压测进程，因参数引号在 PowerShell → docker.exe 传递中被剥离，`pkill -f while` 误匹配并终止了容器 PID 1（`Status=exited`、退出码 143）。该缺陷已被脚本内的运行态校验捕获并判为 FAIL（未归档），修复方式即上文的自限时压测。
