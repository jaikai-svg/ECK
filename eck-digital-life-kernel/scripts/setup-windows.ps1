$ErrorActionPreference = "Stop"

Write-Host "ECK Digital Life Kernel v0.1 setup" -ForegroundColor Green

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI not found. Install Docker Desktop and enable WSL2 integration."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Set ECK_OLLAMA_MODEL to a model already installed in Ollama." -ForegroundColor Yellow
}

docker compose build
Write-Host "Build complete. Run .\scripts\start-windows.ps1" -ForegroundColor Green

