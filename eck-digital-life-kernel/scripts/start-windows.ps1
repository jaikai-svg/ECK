$ErrorActionPreference = "Stop"
docker compose up -d
Write-Host "ECK is starting at http://127.0.0.1:8420" -ForegroundColor Green
docker compose ps

