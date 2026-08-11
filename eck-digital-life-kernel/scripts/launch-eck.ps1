$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$baseUrl = "http://127.0.0.1:8420/"
$logPath = Join-Path $repoRoot "workspace\launcher.log"

function Test-EckApi {
    try {
        Invoke-RestMethod -Uri "${baseUrl}health" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
if (-not (Test-EckApi)) {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start-eck.ps1") | Add-Content -LiteralPath $logPath
    } catch {
        $_ | Out-String | Add-Content -LiteralPath $logPath
        throw
    }
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(5)
    while ([DateTimeOffset]::UtcNow -lt $deadline -and -not (Test-EckApi)) {
        Start-Sleep -Seconds 2
    }
}
if (-not (Test-EckApi)) {
    throw "ECK did not become ready within five minutes. Inspect workspace/eck.err.log."
}
Start-Process $baseUrl
