param(
    [switch]$Wait
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$workspace = Join-Path $repoRoot "workspace"
$pidPath = Join-Path $workspace "eck.pid.json"
$stdoutPath = Join-Path $workspace "eck.out.log"
$stderrPath = Join-Path $workspace "eck.err.log"
$baseUrl = "http://127.0.0.1:8420"
$supervisor = Join-Path $PSScriptRoot "supervise-eck.ps1"
$databasePath = Join-Path $repoRoot "data\eck.db"

function Test-EckApi {
    try {
        Invoke-RestMethod -Uri "$baseUrl/v1/kernel/status" -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (Test-EckApi) {
    Write-Output "ECK_READY $baseUrl"
    exit 0
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "ECK virtual environment not found: $python"
}
New-Item -ItemType Directory -Path $workspace -Force | Out-Null
$pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")

$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", ('"' + $supervisor + '"'),
    "-PythonPath", ('"' + $python + '"'),
    "-RepoRoot", ('"' + $repoRoot + '"'),
    "-DatabasePath", ('"' + $databasePath + '"'),
    "-HostName", "127.0.0.1", "-Port", "8420"
)
$process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
    -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
$supervisorExecutable = (Get-Process -Id $process.Id -ErrorAction Stop).Path
@{
    pid = $process.Id
    started_at = [DateTimeOffset]::UtcNow.ToString("o")
    base_url = $baseUrl
    python = $python
    process_kind = "powershell-supervisor"
    process_path = $supervisorExecutable
} | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding utf8
Write-Output "ECK_STARTING PID=$($process.Id)"

if ($Wait) {
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(2)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            $detail = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Tail 50 | Out-String } else { "No error log." }
            throw "ECK exited with code $($process.ExitCode).`n$detail"
        }
        if (Test-EckApi) {
            Write-Output "ECK_READY $baseUrl"
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    throw "ECK did not become ready within two minutes."
}
