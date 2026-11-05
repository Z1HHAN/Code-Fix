<#
docker-isolation-evidence 追加隔离子项测试（真实 Docker 采集，无离线替身）

覆盖三项：
  1) tmpfs 持久化：主容器写 /tmp -> 销毁容器 -> 新容器同配置启动 -> 文件不存在
  2) Unix socket 隔离：主容器内访问 /var/run/docker.sock 必须失败
  3) 资源限制：docker inspect + docker stats + cgroup v2 内核计数，实测 CPU 0.50 与内存 256MiB 上限

约束：
  - 只用 01-config/compose.yaml，不叠加任何 runtime override；
  - 任一项判据不满足即以非 0 退出，不生成“假 PASS”证据；
  - 证据中不写入宿主机绝对路径。
#>
[CmdletBinding()]
param(
    [string]$EvidenceRoot = '',
    [int]$CpuSampleCount = 8,
    [int]$MemorySampleCount = 18
)

$ErrorActionPreference = 'SilentlyContinue'
if (-not $EvidenceRoot) {
    $EvidenceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\evidence\docker-isolation\qualification')).Path
}
Set-Location $EvidenceRoot
$compose = '01-config/compose.yaml'
$main = 'docker-isolation-test'
$expected = @{ NanoCpus = 500000000; Memory = 268435456; MemorySwap = 268435456; PidsLimit = 128 }
$failures = New-Object System.Collections.Generic.List[string]

function UtcNow { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }

function Invoke-Docker {
    param([string]$Rendered, [string[]]$Arguments)
    $raw = & docker @Arguments 2>&1
    $code = $LASTEXITCODE
    # 只取每条 stderr/stdout 的文本，避免 ErrorRecord 把脚本路径写进证据
    $text = (($raw | ForEach-Object { $_.ToString() }) -join "`n").TrimEnd()
    return [pscustomobject]@{ Rendered = $Rendered; Output = $text; Code = $code }
}

function Add-Block {
    param([System.Text.StringBuilder]$Builder, [string]$Title, $Result)
    if (-not $Result) { return }
    [void]$Builder.AppendLine("### $Title")
    [void]$Builder.AppendLine('~~~text')
    [void]$Builder.AppendLine('$ ' + $Result.Rendered)
    if ($Result.Output) { [void]$Builder.AppendLine($Result.Output) }
    [void]$Builder.AppendLine("exit_code=$($Result.Code)")
    [void]$Builder.AppendLine('~~~')
    [void]$Builder.AppendLine('')
}

function Container-Id { ((& docker inspect $main --format '{{.Id}}') | Out-String).Trim() }
function Metric([string]$Text, [string]$Name) {
    $m = [regex]::Match($Text, "(?m)^$Name\s+(\d+)\s*$")
    if ($m.Success) { return [long]$m.Groups[1].Value }
    return -1
}

# ---------------------------------------------------------------- 公共前置
$overrideFiles = Get-ChildItem -Force -File -Filter '*override*.y*ml' | Where-Object { $_.Name -like '.runtime*' }
if ($overrideFiles) { throw "发现 runtime override 文件，拒绝采集：$($overrideFiles.Name -join ', ')" }

$started = UtcNow
& docker compose -f $compose up -d --no-deps --force-recreate isolation-test | Out-Null
if ($LASTEXITCODE -ne 0) { throw '容器启动失败' }
$imageId = ((& docker inspect $main --format '{{.Image}}') | Out-String).Trim()
$imageRef = ((& docker inspect $main --format '{{.Config.Image}}') | Out-String).Trim()
$baseHeader = @(
    "采集时间 UTC：$started",
    "宿主机：Docker Desktop (WSL2 Linux backend)",
    "容器：$main",
    "镜像：$imageRef",
    "镜像 ID：$imageId",
    "约束：仅使用 $compose，不加载任何 runtime override",
    ''
)

# ---------------------------------------------------------------- 1. tmpfs 持久化
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine('# tmpfs 持久化测试证据')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 测试目的')
[void]$sb.AppendLine('证明容器内可写目录只有 tmpfs：写入的数据随容器销毁而消失，不会跨容器实例持久化。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 执行命令')
[void]$sb.AppendLine('~~~bash')
[void]$sb.AppendLine('docker compose -f 01-config/compose.yaml up -d --no-deps --force-recreate isolation-test')
[void]$sb.AppendLine('docker inspect docker-isolation-test --format ''{{json .HostConfig.Tmpfs}}''')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''echo persistence-probe > /tmp/persistence-probe.txt; cat /tmp/persistence-probe.txt''')
[void]$sb.AppendLine('docker rm -f docker-isolation-test')
[void]$sb.AppendLine('docker compose -f 01-config/compose.yaml up -d --no-deps isolation-test')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''test -e /tmp/persistence-probe.txt; echo test_e_exit=$?''')
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 预期结果')
[void]$sb.AppendLine('- `/tmp`、`/run` 为 tmpfs 挂载，根文件系统只读；')
[void]$sb.AppendLine('- 第一次容器实例写入成功；')
[void]$sb.AppendLine('- 销毁后用同一 compose 启动的新实例中该文件不存在（`test -e` 返回 1）。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 实际测试结果')
[void]$sb.AppendLine('~~~text')
$baseHeader | ForEach-Object { [void]$sb.AppendLine($_) }
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')
Add-Block $sb 'tmpfs 声明（HostConfig.Tmpfs）' (Invoke-Docker 'docker inspect docker-isolation-test --format ''{{json .HostConfig.Tmpfs}}''' @('inspect', $main, '--format', '{{json .HostConfig.Tmpfs}}'))
Add-Block $sb '容器内 /tmp 与 /run 挂载' (Invoke-Docker 'docker exec docker-isolation-test mount | grep -E '' /(tmp|run) ''' @('exec', $main, 'sh', '-c', "mount | grep -E ' /(tmp|run) '"))
$firstId = Container-Id
$write1 = Invoke-Docker 'docker exec docker-isolation-test sh -c ''echo persistence-probe-2026-11-05 > /tmp/persistence-probe.txt; ls -l /tmp/persistence-probe.txt; cat /tmp/persistence-probe.txt''' @('exec', $main, 'sh', '-c', 'echo persistence-probe-2026-11-05 > /tmp/persistence-probe.txt; ls -l /tmp/persistence-probe.txt; cat /tmp/persistence-probe.txt')
Add-Block $sb '首次写入（容器实例 1）' $write1
Add-Block $sb '销毁容器实例 1' (Invoke-Docker 'docker rm -f docker-isolation-test' @('rm', '-f', $main))
$recreate = Invoke-Docker 'docker compose -f 01-config/compose.yaml up -d --no-deps isolation-test' @('compose', '-f', $compose, 'up', '-d', '--no-deps', 'isolation-test')
Add-Block $sb '同配置重新启动容器实例 2' $recreate
$secondId = Container-Id
[void]$sb.AppendLine('容器实例比对：')
[void]$sb.AppendLine('~~~text')
[void]$sb.AppendLine("实例 1 ID：$firstId")
[void]$sb.AppendLine("实例 2 ID：$secondId")
[void]$sb.AppendLine("实例不同：$($firstId -ne $secondId)")
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')
$probe = Invoke-Docker 'docker exec docker-isolation-test sh -c ''ls -l /tmp/persistence-probe.txt; echo ls_exit=$?; test -e /tmp/persistence-probe.txt; echo test_e_exit=$?''' @('exec', $main, 'sh', '-c', 'ls -l /tmp/persistence-probe.txt; echo ls_exit=$?; test -e /tmp/persistence-probe.txt; echo test_e_exit=$?')
Add-Block $sb '新实例中查找探测文件' $probe
Add-Block $sb '新实例 /tmp 剩余文件列表' (Invoke-Docker 'docker exec docker-isolation-test sh -c ''ls -la /tmp''' @('exec', $main, 'sh', '-c', 'ls -la /tmp'))
$tmpfsPass = ($write1.Code -eq 0) -and ($probe.Output -match 'test_e_exit=1') -and ($firstId -ne $secondId) -and ($firstId.Length -ge 12)
if (-not $tmpfsPass) { $failures.Add('tmpfs 持久化判据未满足') }
[void]$sb.AppendLine('## 结论')
[void]$sb.AppendLine('')
if ($tmpfsPass) {
    [void]$sb.AppendLine('PASS：实例 1 写入 `/tmp/persistence-probe.txt` 成功；销毁容器后以同一 `01-config/compose.yaml` 启动的实例 2（容器 ID 不同）中该文件不存在，`test -e` 返回 1。证明写入点仅为 tmpfs，随容器销毁回收，不具备跨实例持久化能力。')
} else {
    [void]$sb.AppendLine('FAIL：实例 1 写入或实例 2 复查未达到预期，详见上方原始输出。')
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('敏感信息处理：仅包含容器 ID 与退出码，不含宿主机路径/账户/凭据。')
[void]$sb.AppendLine('')
Set-Content -Path '03-tests/tmpfs-persistence-tests.txt' -Value $sb.ToString() -Encoding UTF8
Write-Host "tmpfs persistence: pass=$tmpfsPass instance1=$firstId instance2=$secondId"

# ---------------------------------------------------------------- 2. Unix socket 隔离
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine('# Unix socket 隔离测试证据')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 测试目的')
[void]$sb.AppendLine('证明主容器内不存在宿主 Docker daemon 的控制套接字，无法通过 `/var/run/docker.sock` 越权控制宿主机 Docker。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 执行命令')
[void]$sb.AppendLine('~~~bash')
[void]$sb.AppendLine('docker inspect docker-isolation-test --format ''{{json .Mounts}}''')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''ls -l /var/run/docker.sock''')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''[ -S /var/run/docker.sock ] && echo SOCKET_PRESENT || echo SOCKET_ABSENT''')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''grep -c docker.sock /proc/1/mountinfo''')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''cat /var/run/docker.sock > /dev/null''')
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 预期结果')
[void]$sb.AppendLine('- `.Mounts` 中不存在 `docker.sock` 的 bind 挂载；')
[void]$sb.AppendLine('- `/var/run/docker.sock` 不存在（`ls` 失败）且不是 socket（`SOCKET_ABSENT`）；')
[void]$sb.AppendLine('- `/proc/1/mountinfo` 中 `docker.sock` 计数为 0；')
[void]$sb.AppendLine('- 任何对套接字的读取尝试以非 0 退出码失败。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 实际测试结果')
[void]$sb.AppendLine('~~~text')
$baseHeader | ForEach-Object { [void]$sb.AppendLine($_) }
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')
$mounts = Invoke-Docker 'docker inspect docker-isolation-test --format ''{{json .Mounts}}''' @('inspect', $main, '--format', '{{json .Mounts}}')
Add-Block $sb '容器挂载列表（无 docker.sock）' $mounts
$sockLs = Invoke-Docker 'docker exec docker-isolation-test sh -c ''ls -l /var/run/docker.sock''' @('exec', $main, 'sh', '-c', 'ls -l /var/run/docker.sock')
Add-Block $sb '访问 /var/run/docker.sock' $sockLs
$sockType = Invoke-Docker 'docker exec docker-isolation-test sh -c ''[ -S /var/run/docker.sock ] && echo SOCKET_PRESENT || echo SOCKET_ABSENT''' @('exec', $main, 'sh', '-c', '[ -S /var/run/docker.sock ] && echo SOCKET_PRESENT || echo SOCKET_ABSENT')
Add-Block $sb '套接字类型判定' $sockType
$sockMount = Invoke-Docker 'docker exec docker-isolation-test sh -c ''grep -c docker.sock /proc/1/mountinfo''' @('exec', $main, 'sh', '-c', 'grep -c docker.sock /proc/1/mountinfo')
Add-Block $sb 'mountinfo 中 docker.sock 计数' $sockMount
$sockRead = Invoke-Docker 'docker exec docker-isolation-test sh -c ''cat /var/run/docker.sock > /dev/null''' @('exec', $main, 'sh', '-c', 'cat /var/run/docker.sock > /dev/null')
Add-Block $sb '实际读取套接字尝试' $sockRead
Add-Block $sb '/var/run 目录列表' (Invoke-Docker 'docker exec docker-isolation-test sh -c ''ls -la /var/run''' @('exec', $main, 'sh', '-c', 'ls -la /var/run'))
$socketPass = ($mounts.Output -eq '[]') -and ($sockType.Output -match 'SOCKET_ABSENT') -and ($sockMount.Output -match '^\s*0\s*$') -and ($sockRead.Code -ne 0)
if (-not $socketPass) { $failures.Add('Unix socket 隔离判据未满足') }
[void]$sb.AppendLine('## 结论')
[void]$sb.AppendLine('')
if ($socketPass) {
    [void]$sb.AppendLine('PASS：容器内 `/var/run/docker.sock` 不存在，`[ -S ]` 判定为 `SOCKET_ABSENT`，`/proc/1/mountinfo` 中无 `docker.sock` 挂载记录，读取尝试以非 0 退出码失败；`.Mounts` 中也没有任何指向宿主 Docker socket 的 bind 挂载。主容器无法通过 Docker API 越权控制宿主机。')
} else {
    [void]$sb.AppendLine('FAIL：套接字不存在性、mountinfo 计数或读取失败判据未全部满足，详见上方原始输出。')
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('敏感信息处理：仅保留容器内路径与退出码，不含宿主 socket 绝对路径（Windows 命名管道）或 daemon 凭据。')
[void]$sb.AppendLine('')
Set-Content -Path '03-tests/unix-socket-tests.txt' -Value $sb.ToString() -Encoding UTF8
Write-Host "unix socket: pass=$socketPass"

# ---------------------------------------------------------------- 3. 资源限制
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine('# 资源限制测试证据（CPU 配额 / 内存上限）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 测试目的')
[void]$sb.AppendLine('用 `docker inspect` 核对声明的资源限制，并用 `docker stats` 与 cgroup v2 内核计数实测 CPU 配额和内存上限确实生效，防止单容器耗尽宿主机资源。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 执行命令')
[void]$sb.AppendLine('~~~bash')
[void]$sb.AppendLine('docker inspect docker-isolation-test --format ''NanoCpus={{.HostConfig.NanoCpus}} CpuQuota={{.HostConfig.CpuQuota}} CpuPeriod={{.HostConfig.CpuPeriod}} Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} PidsLimit={{.HostConfig.PidsLimit}}''')
[void]$sb.AppendLine('docker stats --no-stream --format ''{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|memperc={{.MemPerc}}|pids={{.PIDs}}'' docker-isolation-test')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''timeout 10 yes >/dev/null''')
[void]$sb.AppendLine('docker exec docker-isolation-test cat /sys/fs/cgroup/memory.events')
[void]$sb.AppendLine('docker exec docker-isolation-test sh -c ''y=0123456789abcdef0123456789abcdef; x=$y; while :; do x=$x$x; done''')
[void]$sb.AppendLine('docker inspect docker-isolation-test --format ''Status={{.State.Status}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}''')
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 预期结果')
[void]$sb.AppendLine('- `NanoCpus=500000000`（0.50 CPU）、`Memory=268435456`（256 MiB）、`MemorySwap=268435456`（禁止额外 swap）、`PidsLimit=128`；')
[void]$sb.AppendLine('- 单线程忙循环时 `docker stats` 的 `CPUPerc` 收敛在 50% 附近，而不是 100%；')
[void]$sb.AppendLine('- 内存压测进程被 cgroup OOM 杀死（`docker exec` 退出码 137），`memory.events` 的 `oom_kill` 计数递增，`memory.peak` 达到 `memory.max`；')
[void]$sb.AppendLine('- 压测结束后 CPU 回落，容器仍为 running（限制生效但不影响正常使用）。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 实际测试结果')
[void]$sb.AppendLine('~~~text')
$baseHeader | ForEach-Object { [void]$sb.AppendLine($_) }
[void]$sb.AppendLine('~~~')
[void]$sb.AppendLine('')

$inspectRes = Invoke-Docker 'docker inspect docker-isolation-test --format ''NanoCpus=... Memory=... PidsLimit=...''' @('inspect', $main, '--format', 'NanoCpus={{.HostConfig.NanoCpus}} CpuQuota={{.HostConfig.CpuQuota}} CpuPeriod={{.HostConfig.CpuPeriod}} Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} PidsLimit={{.HostConfig.PidsLimit}}')
Add-Block $sb '声明的资源限制（docker inspect）' $inspectRes
$nano = [long]([regex]::Match($inspectRes.Output, 'NanoCpus=(\d+)').Groups[1].Value)
$memLimit = [long]([regex]::Match($inspectRes.Output, 'Memory=(\d+)').Groups[1].Value)
$swapLimit = [long]([regex]::Match($inspectRes.Output, 'MemorySwap=(\d+)').Groups[1].Value)
$pidsLimit = [long]([regex]::Match($inspectRes.Output, 'PidsLimit=(\d+)').Groups[1].Value)
$inspectPass = ($nano -eq $expected.NanoCpus) -and ($memLimit -eq $expected.Memory) -and ($swapLimit -eq $expected.MemorySwap) -and ($pidsLimit -eq $expected.PidsLimit)
if (-not $inspectPass) { $failures.Add("资源限制声明与预期不一致：NanoCpus=$nano Memory=$memLimit MemorySwap=$swapLimit PidsLimit=$pidsLimit") }

$statsFormat = '{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|memperc={{.MemPerc}}|pids={{.PIDs}}'
$idle = Invoke-Docker 'docker stats --no-stream --format ''{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|pids={{.PIDs}}'' docker-isolation-test' @('stats', '--no-stream', '--format', $statsFormat, $main)
Add-Block $sb '空闲基线（docker stats --no-stream）' $idle

# CPU 压测在容器内自限时 10 秒（不依赖 pkill，避免误杀 PID 1）；宿主机侧并发采样，结束后自然回落。
$cpuLog = Join-Path $env:TEMP ('codefix-cpustats-' + [guid]::NewGuid().ToString('N') + '.txt')
$cpuJob = Start-Job -ScriptBlock {
    param($Name, $Format, $Path, $Count)
    for ($i = 0; $i -lt $Count; $i++) {
        $line = (& docker stats --no-stream --format $Format $Name) -join ' '
        Add-Content -Path $Path -Value $line
        Start-Sleep -Milliseconds 700
    }
} -ArgumentList $main, $statsFormat, $cpuLog, $CpuSampleCount
Start-Sleep -Milliseconds 500
$cpuLoad = Invoke-Docker 'docker exec docker-isolation-test sh -c ''timeout 10 yes >/dev/null''' @('exec', $main, 'sh', '-c', 'timeout 10 yes >/dev/null')
    [void]$sb.AppendLine('CPU 压测进程（容器内 timeout 10 自限时，到时以 SIGTERM 结束）')
Wait-Job $cpuJob -Timeout 60 | Out-Null
Stop-Job $cpuJob -ErrorAction SilentlyContinue
Remove-Job $cpuJob -Force -ErrorAction SilentlyContinue
$cpuRows = if (Test-Path $cpuLog) { Get-Content $cpuLog | Where-Object { $_ } } else { @() }
Remove-Item $cpuLog -Force -ErrorAction SilentlyContinue
$cpuValues = @()
foreach ($line in $cpuRows) {
    $m = [regex]::Match($line, 'cpu=([\d\.]+)%')
    if ($m.Success) { $cpuValues += [double]$m.Groups[1].Value }
}
Add-Block $sb 'CPU 配额实测：单线程忙循环期间的 docker stats 采样' ([pscustomobject]@{
    Rendered = 'while sampling; docker stats --no-stream --format ''{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|pids={{.PIDs}}'' docker-isolation-test'
    Output = ($cpuRows -join "`n")
    Code = 0
})
$busySamples = @($cpuValues | Where-Object { $_ -ge 40 -and $_ -le 60 })
$cpuPass = ($cpuValues.Count -ge 4) -and ($busySamples.Count -ge [math]::Max(3, [math]::Ceiling($cpuValues.Count / 2))) -and (@($cpuValues | Where-Object { $_ -gt 90 }).Count -eq 0)
if (-not $cpuPass) { $failures.Add("CPU 配额判据未满足：samples=$($cpuValues -join ',')") }
[void]$sb.AppendLine("CPU 采样统计：样本数=$($cpuValues.Count)，落在 40%–60% 区间=$($busySamples.Count)，超过 90% 的样本=$(@($cpuValues | Where-Object { $_ -gt 90 }).Count)")
[void]$sb.AppendLine('')

Start-Sleep -Seconds 2
$afterCpu = Invoke-Docker 'docker stats --no-stream --format ''{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|pids={{.PIDs}}'' docker-isolation-test' @('stats', '--no-stream', '--format', $statsFormat, $main)
Add-Block $sb '压测结束后回落' $afterCpu

$memMaxRun = Invoke-Docker 'docker exec docker-isolation-test cat /sys/fs/cgroup/memory.max' @('exec', $main, 'cat', '/sys/fs/cgroup/memory.max')
Add-Block $sb 'cgroup v2 memory.max' $memMaxRun
$eventsBefore = Invoke-Docker 'docker exec docker-isolation-test cat /sys/fs/cgroup/memory.events' @('exec', $main, 'cat', '/sys/fs/cgroup/memory.events')
Add-Block $sb '内存压测前：memory.events 基线' $eventsBefore
$stateBefore = Invoke-Docker 'docker inspect docker-isolation-test --format ''Status={{.State.Status}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}''' @('inspect', $main, '--format', 'Status={{.State.Status}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}')
Add-Block $sb '内存压测前：容器状态' $stateBefore
$oomBefore = Metric $eventsBefore.Output 'oom_kill'
$oomEventBefore = Metric $eventsBefore.Output 'oom'

$memLog = Join-Path $env:TEMP ('codefix-memstats-' + [guid]::NewGuid().ToString('N') + '.txt')
$sampler = Start-Job -ScriptBlock {
    param($Name, $Format, $Path, $Count)
    for ($i = 0; $i -lt $Count; $i++) {
        $line = (& docker stats --no-stream --format $Format $Name) -join ' '
        Add-Content -Path $Path -Value ((Get-Date).ToUniversalTime().ToString('HH:mm:ss.fff') + ' ' + $line)
        Start-Sleep -Milliseconds 700
    }
} -ArgumentList $main, $statsFormat, $memLog, $MemorySampleCount
Start-Sleep -Seconds 1
$hog = Invoke-Docker 'docker exec docker-isolation-test sh -c ''y=0123456789abcdef0123456789abcdef; x=$y; while :; do x=$x$x; done''' @('exec', $main, 'sh', '-c', 'y=0123456789abcdef0123456789abcdef; x=$y; while :; do x=$x$x; done')
Add-Block $sb '内存上限实测：持续倍增分配直至 cgroup 上限' $hog
Wait-Job $sampler -Timeout 90 | Out-Null
Stop-Job $sampler -ErrorAction SilentlyContinue
Remove-Job $sampler -Force -ErrorAction SilentlyContinue
$memSamples = if (Test-Path $memLog) { Get-Content $memLog } else { @('(no samples)') }
Remove-Item $memLog -Force -ErrorAction SilentlyContinue
Add-Block $sb '内存上限实测：docker stats 采样' ([pscustomobject]@{
    Rendered = 'while sampling; docker stats --no-stream --format ''{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|memperc={{.MemPerc}}|pids={{.PIDs}}'' docker-isolation-test'
    Output = ($memSamples -join "`n")
    Code = 0
})

Start-Sleep -Seconds 2
$eventsAfter = Invoke-Docker 'docker exec docker-isolation-test cat /sys/fs/cgroup/memory.events' @('exec', $main, 'cat', '/sys/fs/cgroup/memory.events')
Add-Block $sb '内存压测后：memory.events' $eventsAfter
$peakRun = Invoke-Docker 'docker exec docker-isolation-test cat /sys/fs/cgroup/memory.peak' @('exec', $main, 'cat', '/sys/fs/cgroup/memory.peak')
Add-Block $sb '内存压测后：memory.peak' $peakRun
$stateAfter = Invoke-Docker 'docker inspect docker-isolation-test --format ''Status={{.State.Status}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}''' @('inspect', $main, '--format', 'Status={{.State.Status}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}')
Add-Block $sb '内存压测后：容器状态' $stateAfter
Add-Block $sb '内存压测后：docker stats' (Invoke-Docker 'docker stats --no-stream --format ''{{.Name}}|cpu={{.CPUPerc}}|mem={{.MemUsage}}|memperc={{.MemPerc}}|pids={{.PIDs}}'' docker-isolation-test' @('stats', '--no-stream', '--format', $statsFormat, $main))

$oomAfter = Metric $eventsAfter.Output 'oom_kill'
$oomEventAfter = Metric $eventsAfter.Output 'oom'
$peak = [long]($peakRun.Output.Trim())
$memPass = ($hog.Code -eq 137) -and ($oomBefore -ge 0) -and ($oomAfter -ge ($oomBefore + 1)) -and ($oomEventAfter -ge ($oomEventBefore + 1)) -and ($peak -ge $memLimit) -and ($stateAfter.Output -match 'Status=running')
if (-not $memPass) { $failures.Add("内存上限判据未满足：exec_rc=$($hog.Code) oom_kill=$oomBefore->$oomAfter oom=$oomEventBefore->$oomEventAfter peak=$peak") }
[void]$sb.AppendLine("内存压测统计：docker exec 退出码=$($hog.Code)，oom_kill 计数=$oomBefore→$oomAfter，oom 事件=$oomEventBefore→$oomEventAfter，memory.peak=$peak（memory.max=$($memMaxRun.Output.Trim())）")
[void]$sb.AppendLine('')

[void]$sb.AppendLine('## 结论')
[void]$sb.AppendLine('')
if ($inspectPass -and $cpuPass -and $memPass) {
    [void]$sb.AppendLine("PASS：``docker inspect`` 显示 ``NanoCpus=500000000``（0.50 CPU）、``Memory=268435456``、``MemorySwap=268435456``、``PidsLimit=128``，与 ``01-config/compose.yaml`` 声明一致。单线程忙循环期间 ``docker stats`` 的 CPU 占用收敛在 50% 附近（$($busySamples.Count)/$($cpuValues.Count) 个样本落在 40%–60%，无样本超过 90%），压测结束后回落。内存压测进程持续分配直至被 cgroup OOM 杀死，``docker exec`` 退出码 $($hog.Code)（128+SIGKILL），内核 ``memory.events`` 的 ``oom_kill`` 计数由 $oomBefore 增至 $oomAfter，``memory.peak`` 达到 $peak 字节（``memory.max``=$(($memMaxRun.Output).Trim())）。CPU 配额与内存上限均实测生效。")
} else {
    [void]$sb.AppendLine("FAIL：资源限制判据未全部满足（inspect=$inspectPass cpu=$cpuPass memory=$memPass），详见上方原始输出。本文件不得作为通过证据。")
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('说明：`docker stats` 的 `CPUPerc` 为采样窗口内的平均占用，0.50 单核算力下理论峰值为 50%；宿主机调度抖动会带来几个百分点偏差。')
[void]$sb.AppendLine('说明：被 OOM 杀死的是 `docker exec` 启动的压测进程，容器 PID 1 仍存活，因此 `State.Status=running`；Docker 的 `State.OOMKilled` 反映 cgroup 最近一次 OOM 事件，与内核 `memory.events` 计数互相印证。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('敏感信息处理：仅包含容器名、资源数值与退出码，不含宿主机 CPU 型号、进程列表或账户信息。')
[void]$sb.AppendLine('')
Set-Content -Path '03-tests/resource-limit-tests.txt' -Value $sb.ToString() -Encoding UTF8
Write-Host "resource limits: pass=$($inspectPass -and $cpuPass -and $memPass) exec_rc=$($hog.Code) oom_kill=$oomBefore->$oomAfter peak=$peak"

if ($failures.Count -gt 0) {
    Write-Host 'RESULT: FAIL'
    $failures | ForEach-Object { Write-Host " - $_" }
    exit 1
}
Write-Host ("RESULT: PASS at " + (UtcNow))
