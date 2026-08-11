param(
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

Write-Host "ECK local installer" -ForegroundColor Cyan
Set-Location -LiteralPath $repoRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            & $python.Source -3 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw "Python could not create the virtual environment." }
        }
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) { throw "Python 3.11+ was not found. Install Python and retry." }
        & $python.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Python could not create the virtual environment." }
    }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -e ".[rag]"
if ($LASTEXITCODE -ne 0) { throw "ECK dependency installation failed." }
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot ".env"))) {
    Copy-Item -LiteralPath (Join-Path $repoRoot ".env.example") -Destination (Join-Path $repoRoot ".env")
}

try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (
        Join-Path $PSScriptRoot "install-github-cli.ps1"
    )
    if ($LASTEXITCODE -ne 0) { throw "GitHub CLI verification failed." }
} catch {
    Write-Warning "GitHub CLI was not installed. Local ECK remains usable, but verified GitHub publication is unavailable."
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    try {
        & $docker.Source version --format "{{.Server.Version}}" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Docker daemon is unavailable." }
        & $docker.Source build --file docker/skill-worker/Dockerfile --tag eck-skill-worker:0.1.0 .
        if ($LASTEXITCODE -ne 0) { throw "Docker skill worker build failed." }
    } catch {
        Write-Warning "The Docker worker was not built. Start Docker Desktop and rebuild it from the system page."
    }
} else {
    Write-Warning "Docker Desktop is missing. The core can start, but new skills cannot pass isolated validation."
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "create-desktop-shortcut.ps1")
if ($LASTEXITCODE -ne 0) { throw "Desktop shortcut creation failed." }
if (-not $NoStart) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "launch-eck.ps1")
    if ($LASTEXITCODE -ne 0) { throw "ECK launcher failed." }
}
Write-Host "Installation complete. Model weights are not bundled; ECK uses the local model settings in .env." -ForegroundColor Green
