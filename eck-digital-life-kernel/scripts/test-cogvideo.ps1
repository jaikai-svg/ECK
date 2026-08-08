param(
    [int]$Steps = 2,
    [int]$Seed = 31337
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot "workspace\cogvideo\.conda\python.exe"
$worker = Join-Path $repoRoot "scripts\run_cogvideo_engine.py"
$requestPath = Join-Path $repoRoot "workspace\cogvideo\smoke-request.json"
$reportPath = Join-Path $repoRoot "workspace\cogvideo\verified-runtime.json"
$outputPath = Join-Path $repoRoot "workspace\generated_videos\cogvideox-2b-smoke-1s.mp4"

if (-not (Test-Path $python)) {
    throw "CogVideo environment is missing. Run scripts/setup-cogvideo.ps1 first."
}

$request = @{
    model_dir = (Join-Path $repoRoot "workspace\cogvideo\model")
    prompt = "A golden retriever happily runs after a red ball on a sunny green lawn, natural motion, cinematic light, detailed fur"
    negative_prompt = "distorted, blurry, duplicate animal, extra limbs, text, watermark"
    frames = 9
    fps = 8
    steps = [Math]::Max(1, $Steps)
    guidance_scale = 6.0
    seed = $Seed
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $requestPath,
    ($request | ConvertTo-Json -Depth 4),
    $utf8
)

try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/generate" `
        -ContentType "application/json" `
        -Body '{"model":"qwen3:4b","keep_alive":0}' -TimeoutSec 120 | Out-Null
} catch {
    Write-Warning "Ollama model could not be pre-unloaded: $($_.Exception.Message)"
}
try {
    & (Join-Path $repoRoot "scripts\stop-forge.ps1")
} catch {
    Write-Warning "Forge worker could not be pre-stopped: $($_.Exception.Message)"
}

$json = & $python $worker --request $requestPath --output $outputPath
if ($LASTEXITCODE -ne 0) {
    throw "CogVideo smoke test failed: $json"
}
$result = $json | ConvertFrom-Json
$report = @{
    verified = $true
    verified_at = (Get-Date).ToString("o")
    model = $result.metadata.model
    backend = $result.metadata.backend
    gpu = "NVIDIA GeForce RTX 3060 Laptop GPU"
    gpu_vram_gb = 6
    frames = $result.metadata.frames
    fps = $result.metadata.fps
    nominal_seconds = $result.metadata.seconds
    steps = $result.metadata.steps
    peak_gpu_memory_gb = $result.metadata.peak_gpu_memory_gb
    elapsed_seconds = $result.metadata.elapsed_seconds
    artifact = "workspace/generated_videos/cogvideox-2b-smoke-1s.mp4"
    artifact_sha256 = $result.sha256
}
[System.IO.File]::WriteAllText(
    $reportPath,
    ($report | ConvertTo-Json -Depth 4),
    $utf8
)
$report | ConvertTo-Json -Depth 4
