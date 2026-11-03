param([ValidateSet('serve','check','smoke','budget')][string]$Action = 'check', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = Join-Path $repoRoot 'runs/v3-venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install requirements-lock.txt first.' }
# Process-local only. A approved cumulative CNY limits in model_profiles.json.
# No daily reset, key literals, system environment edits, or automatic API retries.
$env:CODE_FIX_LIVE_APPROVED = '1'
$env:CODE_FIX_SF_APPROVED = '1'
$env:CODE_FIX_SF_REQUEST_SECONDS = '30'
$catalog = Get-Content -LiteralPath (Join-Path $repoRoot 'configs/siliconflow_catalog.json') -Raw -Encoding utf8 | ConvertFrom-Json
foreach ($entry in $catalog.models) {
    if ($null -ne $entry.input_cny_per_million -and $null -ne $entry.output_cny_per_million) {
        $prefix = 'CODE_FIX_SF_' + $entry.alias.ToUpperInvariant()
        [Environment]::SetEnvironmentVariable($prefix + '_INPUT_CNY', [string]$entry.input_cny_per_million, 'Process')
        [Environment]::SetEnvironmentVariable($prefix + '_OUTPUT_CNY', [string]$entry.output_cny_per_million, 'Process')
    }
}
Push-Location -LiteralPath $repoRoot
try {
    switch ($Action) {
        'serve' { & $pythonPath -B -X utf8 -m app.cli serve --port $Port }
        'check' { & $pythonPath -B -X utf8 -m app.cli provider-check }
        'smoke' { & $pythonPath -B -X utf8 -m app.cli provider-smoke --profile sf_account1_v32 }
        'budget' { & $pythonPath -B -X utf8 -m app.cli budget-status }
    }
    if ($LASTEXITCODE -ne 0) { throw 'Command blocked or failed. Read the sanitized result; no automatic retry.' }
} finally { Pop-Location }
