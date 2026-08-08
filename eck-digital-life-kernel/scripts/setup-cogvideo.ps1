param(
    [switch]$SkipModelDownload
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = (Resolve-Path (Join-Path $repoRoot "../..")).Path
$runtimeRoot = Join-Path $repoRoot "workspace\cogvideo"
$environmentRoot = Join-Path $runtimeRoot ".conda"
$modelCache = Join-Path $workspaceRoot ".eck-model-cache\cogvideo\CogVideoX-2b"
$modelLink = Join-Path $runtimeRoot "model"
$python = Join-Path $environmentRoot "python.exe"

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
if (-not (Test-Path $python)) {
    conda create -p $environmentRoot python=3.11 pip -y
    & $python -m pip install torch==2.5.1 torchvision==0.20.1 `
        --index-url https://download.pytorch.org/whl/cu121
    & $python -m pip install -r (Join-Path $repoRoot "config\cogvideo-requirements.txt")
}
if (-not $SkipModelDownload) {
    New-Item -ItemType Directory -Path $modelCache -Force | Out-Null
    $env:HF_HUB_DISABLE_XET = "1"
    & $python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='zai-org/CogVideoX-2b', local_dir=r'$modelCache', max_workers=3)"
}
if ((Test-Path $modelLink) -and ((Get-Item $modelLink).LinkType -ne "Junction")) {
    throw "CogVideo model path exists but is not a junction: $modelLink"
}
if (-not (Test-Path $modelLink)) {
    New-Item -ItemType Junction -Path $modelLink -Target $modelCache | Out-Null
}
& $python (Join-Path $repoRoot "scripts\run_cogvideo_engine.py") `
    --self-check --model-dir $modelLink
