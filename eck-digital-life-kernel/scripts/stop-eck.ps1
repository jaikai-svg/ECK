$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $repoRoot "workspace\eck.pid.json"
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output "ECK_NOT_RUNNING"
    exit 0
}

$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if ($process) {
    $expectedProcess = if ($record.process_path) {
        [string]$record.process_path
    } else {
        [string]$record.python
    }
    $expectedProcess = [System.IO.Path]::GetFullPath($expectedProcess)
    if ([System.IO.Path]::GetFullPath($process.Path) -ne $expectedProcess) {
        throw "PID $($record.pid) no longer belongs to the recorded ECK process."
    }
    taskkill.exe /PID $process.Id /T /F | Out-Null
    Start-Sleep -Seconds 1
    if (Get-Process -Id $process.Id -ErrorAction SilentlyContinue) {
        throw "The recorded ECK process tree could not be stopped."
    }
}
Remove-Item -LiteralPath $pidPath -Force
Write-Output "ECK_STOPPED"
