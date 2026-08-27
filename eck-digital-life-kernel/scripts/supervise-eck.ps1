param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$DatabasePath,
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8420
)

$ErrorActionPreference = "Stop"
$workspace = Join-Path $RepoRoot "workspace"
$stdoutPath = Join-Path $workspace "eck.out.log"
$stderrPath = Join-Path $workspace "eck.err.log"
$supervisorLog = Join-Path $workspace "eck-supervisor.log"
$recoveryScript = Join-Path $PSScriptRoot "evolution-recovery.py"
$arguments = @("-m", "eck.cli", "serve", "--host", $HostName, "--port", "$Port")

New-Item -ItemType Directory -Path $workspace -Force | Out-Null

while ($true) {
    "$(Get-Date -Format o) starting ECK" | Add-Content -LiteralPath $supervisorLog
    $process = Start-Process -FilePath $PythonPath -ArgumentList $arguments `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $process.WaitForExit()
    if ($process.ExitCode -eq 0) {
        "$(Get-Date -Format o) ECK stopped cleanly" | Add-Content -LiteralPath $supervisorLog
        exit 0
    }
    "$(Get-Date -Format o) ECK exited with code $($process.ExitCode)" |
        Add-Content -LiteralPath $supervisorLog
    & $PythonPath $recoveryScript --repo-root $RepoRoot --database $DatabasePath `
        --maximum-age-seconds 600 | Add-Content -LiteralPath $supervisorLog
    if ($LASTEXITCODE -ne 20) {
        "$(Get-Date -Format o) no safe automatic rollback authority" |
            Add-Content -LiteralPath $supervisorLog
        exit $process.ExitCode
    }
    "$(Get-Date -Format o) previous commit restored; restarting" |
        Add-Content -LiteralPath $supervisorLog
}
