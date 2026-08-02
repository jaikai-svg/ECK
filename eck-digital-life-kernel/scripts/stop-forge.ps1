$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$forgeRoot = if ($env:ECK_FORGE_ROOT) { $env:ECK_FORGE_ROOT } else { Join-Path $repoRoot "workspace\forge" }
if (-not [System.IO.Path]::IsPathRooted($forgeRoot)) {
    $forgeRoot = Join-Path $repoRoot $forgeRoot
}
$forgeRoot = [System.IO.Path]::GetFullPath($forgeRoot)
$pidPath = Join-Path $forgeRoot "forge.pid.json"
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output "FORGE_NOT_RUNNING"
    exit 0
}
$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if ($process) {
    $expectedPython = [System.IO.Path]::GetFullPath([string]$record.python)
    if ([System.IO.Path]::GetFullPath($process.Path) -ne $expectedPython) {
        throw "PID $($record.pid) no longer belongs to the recorded Forge Python process."
    }
    Stop-Process -Id $process.Id
    $process.WaitForExit(30000)
}
Remove-Item -LiteralPath $pidPath -Force
Write-Output "FORGE_STOPPED"
