param([string]$OutputPath = 'runs/docker-prerequisites.json')
# Read-only inspection: never enables features, installs WSL, or changes BIOS.
$ErrorActionPreference = 'Stop'
$inspection = [ordered]@{ checked_at = (Get-Date).ToString('o'); modifications_performed = $false }
foreach ($query in @(
    @{ key = 'os'; class = 'Win32_OperatingSystem'; fields = @('Caption','OSArchitecture','Version','TotalVisibleMemorySize','FreePhysicalMemory') },
    @{ key = 'processor'; class = 'Win32_Processor'; fields = @('Name','VirtualizationFirmwareEnabled','SecondLevelAddressTranslationExtensions','VMMonitorModeExtensions') },
    @{ key = 'computer'; class = 'Win32_ComputerSystem'; fields = @('HypervisorPresent','TotalPhysicalMemory') }
)) {
    try { $inspection[$query.key] = Get-CimInstance -ClassName $query.class | Select-Object -Property $query.fields }
    catch { $inspection[$query.key] = @{ status = 'unavailable'; error_type = $_.Exception.GetType().Name } }
}
try {
    $inspection['optional_features'] = @(Get-CimInstance Win32_OptionalFeature |
        Where-Object { $_.Name -in @('VirtualMachinePlatform','Microsoft-Windows-Subsystem-Linux','Microsoft-Hyper-V-All') } |
        Select-Object Name,InstallState)
} catch { $inspection['optional_features'] = @{ status = 'unavailable'; error_type = $_.Exception.GetType().Name } }
$inspection['disk_c'] = Get-PSDrive C | Select-Object Used,Free
$inspection['docker_executable'] = (Get-Command docker -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
$inspection['wsl_checks'] = @()
$wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wslCommand) {
    foreach ($arguments in @(@('--status'), @('--version'), @('--list','--verbose'))) {
        $start = [System.Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $wslCommand.Source
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.StandardOutputEncoding = [System.Text.Encoding]::Unicode
        $start.StandardErrorEncoding = [System.Text.Encoding]::Unicode
        foreach ($argument in $arguments) { $start.ArgumentList.Add($argument) }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $start
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $completed = $process.WaitForExit(10000)
        if (-not $completed) { $process.Kill(); $process.WaitForExit() }
        $inspection['wsl_checks'] += @{ arguments = $arguments; exit_code = $process.ExitCode;
            timeout = -not $completed; stdout = $stdout.GetAwaiter().GetResult(); stderr = $stderr.GetAwaiter().GetResult() }
        $process.Dispose()
    }
}
$inspection['conclusion'] = 'Read-only observations; configuration readiness is not Docker isolation acceptance.'
$parent = Split-Path -Parent $OutputPath
if ($parent) { [void](New-Item -ItemType Directory -Path $parent -Force) }
$inspection | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
$inspection | ConvertTo-Json -Depth 8
