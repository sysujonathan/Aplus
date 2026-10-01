$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$runtimeHome = [Environment]::GetEnvironmentVariable('A_WORKBENCH_HOME', 'Process')
if ([string]::IsNullOrWhiteSpace($runtimeHome)) {
    $runtimeHome = [Environment]::GetEnvironmentVariable('A_WORKBENCH_HOME', 'User')
}
if ([string]::IsNullOrWhiteSpace($runtimeHome)) {
    $runtimeHome = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Aplus\runtime'
}
$legacyRecord = Join-Path $appRoot 'runtime\server.json'
$recordPath = Join-Path $runtimeHome 'server.json'
if (-not (Test-Path -LiteralPath $recordPath) -and (Test-Path -LiteralPath $legacyRecord)) {
    $recordPath = $legacyRecord
}
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output 'A is not running from this launcher.'; exit }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$appProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
$expectedApp = Join-Path $appRoot 'app.py'
if ($appProcess -and $appProcess.CommandLine.Contains($expectedApp) -and $appProcess.CommandLine.Contains('streamlit')) {
    Stop-Process -Id $record.pid
    Write-Output 'A stopped. Saved data is preserved. Any unfinished job will be marked interrupted at next launch.'
} else {
    Write-Output 'No matching A process. Nothing was stopped.'
}
