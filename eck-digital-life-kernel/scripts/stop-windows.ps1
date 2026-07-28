$ErrorActionPreference = "Stop"
docker compose down
Write-Host "ECK stopped. Named volumes were preserved." -ForegroundColor Yellow

