param(
    [switch]$KeepArchive
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$framepackRoot = Join-Path $repoRoot "workspace\framepack"
$sourceRoot = Join-Path $framepackRoot "source"
$runtimeRoot = Join-Path $framepackRoot "framepack_cu126_torch26"
$archive = Join-Path $framepackRoot "framepack_cu126_torch26.7z"
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $repoRoot)
$shortCache = Join-Path $workspaceRoot ".eck-model-cache\framepack"
$releaseUrl = "https://github.com/lllyasviel/FramePack/releases/download/windows/framepack_cu126_torch26.7z"

New-Item -ItemType Directory -Path $framepackRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot "demo_gradio.py"))) {
    git clone --depth 1 https://github.com/lllyasviel/FramePack.git $sourceRoot
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "system\python\python.exe"))) {
    if (-not (Test-Path -LiteralPath $archive)) {
        curl.exe -L --fail --retry 3 --continue-at - --output $archive $releaseUrl
    }
    tar.exe -xf $archive -C $framepackRoot
}

$python = Join-Path $runtimeRoot "system\python\python.exe"
$sourceCache = Join-Path $sourceRoot "hf_download"
New-Item -ItemType Directory -Path $shortCache -Force | Out-Null
if (-not (Test-Path -LiteralPath $sourceCache)) {
    New-Item -ItemType Junction -Path $sourceCache -Target $shortCache | Out-Null
}
& $python (Join-Path $repoRoot "scripts\run_framepack_engine.py") --self-check --source-dir $sourceRoot
& $python (Join-Path $repoRoot "scripts\download_framepack_models.py") --source-dir $sourceRoot
if (-not $KeepArchive -and (Test-Path -LiteralPath $archive)) {
    Remove-Item -LiteralPath $archive -Force
}
