param(
    [switch]$Wait
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$forgeRoot = if ($env:ECK_FORGE_ROOT) { $env:ECK_FORGE_ROOT } else { Join-Path $repoRoot "workspace\forge" }
if (-not [System.IO.Path]::IsPathRooted($forgeRoot)) {
    $forgeRoot = Join-Path $repoRoot $forgeRoot
}
$forgeRoot = [System.IO.Path]::GetFullPath($forgeRoot)
$webuiRoot = Join-Path $forgeRoot "webui"
$systemRoot = Join-Path $forgeRoot "system"
$python = Join-Path $systemRoot "python\python.exe"
$port = if ($env:ECK_FORGE_PORT) { [int]$env:ECK_FORGE_PORT } else { 7861 }
$checkpoint = if ($env:ECK_FORGE_CHECKPOINT) { $env:ECK_FORGE_CHECKPOINT } else { "realisticVisionV60B1_v60B1VAE.safetensors" }
$checkpointPath = Join-Path $webuiRoot "models\Stable-diffusion\$checkpoint"
$apiUrl = "http://127.0.0.1:$port/sdapi/v1/options"
$pidPath = Join-Path $forgeRoot "forge.pid.json"
$stdoutPath = Join-Path $forgeRoot "forge.out.log"
$stderrPath = Join-Path $forgeRoot "forge.err.log"

function Test-ForgeApi {
    try {
        Invoke-RestMethod -Uri $apiUrl -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (Test-ForgeApi) {
    Write-Output "FORGE_READY $apiUrl"
    exit 0
}
if (-not (Test-Path -LiteralPath $python)) { throw "Forge Python not found: $python" }
if (-not (Test-Path -LiteralPath (Join-Path $webuiRoot "launch.py"))) { throw "Forge launch.py not found." }
if (-not (Test-Path -LiteralPath $checkpointPath)) { throw "Forge checkpoint not found: $checkpointPath" }

$env:PATH = "$(Join-Path $systemRoot 'git\bin');$(Join-Path $systemRoot 'python');$(Join-Path $systemRoot 'python\Scripts');$env:PATH"
$env:PY_LIBS = "$(Join-Path $systemRoot 'python\Scripts\Lib');$(Join-Path $systemRoot 'python\Scripts\Lib\site-packages')"
$env:PY_PIP = Join-Path $systemRoot "python\Scripts"
$env:SKIP_VENV = "1"
$env:PIP_INSTALLER_LOCATION = Join-Path $systemRoot "python\get-pip.py"
$env:TRANSFORMERS_CACHE = Join-Path $systemRoot "transformers-cache"
$env:HF_HOME = Join-Path $forgeRoot "huggingface-cache"
$safeRepositories = @($webuiRoot) + @(
    Get-ChildItem -LiteralPath $webuiRoot -Directory -Filter ".git" -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Parent.FullName }
)
$safeRepositories = @($safeRepositories | Sort-Object -Unique)
$env:GIT_CONFIG_COUNT = "$($safeRepositories.Count)"
for ($index = 0; $index -lt $safeRepositories.Count; $index++) {
    [Environment]::SetEnvironmentVariable("GIT_CONFIG_KEY_$index", "safe.directory", "Process")
    [Environment]::SetEnvironmentVariable(
        "GIT_CONFIG_VALUE_$index",
        $safeRepositories[$index].Replace("\", "/"),
        "Process"
    )
}
$arguments = @(
    "launch.py",
    "--nowebui",
    "--server-name", "127.0.0.1",
    "--port", "$port",
    "--no-download-sd-model",
    "--cuda-stream",
    "--ckpt", $checkpointPath
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $webuiRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
@{
    pid = $process.Id
    started_at = [DateTimeOffset]::UtcNow.ToString("o")
    api_url = $apiUrl
    checkpoint = $checkpoint
    python = $python
} | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding utf8
Write-Output "FORGE_STARTING PID=$($process.Id)"

if ($Wait) {
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(15)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            $detail = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Tail 40 | Out-String } else { "No error log." }
            throw "Forge exited with code $($process.ExitCode).`n$detail"
        }
        if (Test-ForgeApi) {
            Write-Output "FORGE_READY $apiUrl"
            exit 0
        }
        Start-Sleep -Seconds 2
        $process.Refresh()
    }
    throw "Forge did not become ready within 15 minutes."
}
