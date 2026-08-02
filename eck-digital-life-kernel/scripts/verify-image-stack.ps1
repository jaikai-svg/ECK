$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$forgeRoot = Join-Path $repoRoot "workspace\forge"
$modelRoot = Join-Path $forgeRoot "webui\models"
$expectedFiles = @(
    @{
        Path = "Stable-diffusion\realisticVisionV60B1_v60B1VAE.safetensors"
        Sha256 = "FE7578CB5EE0BE63AA15BAA894AB5D1751FF9B5B25EF611D5FAFB2186D930C30"
    },
    @{
        Path = "Stable-diffusion\chilloutmix_NiPrunedFp16Fix.safetensors"
        Sha256 = "59FFE2243A25C9FE137D590EB3C5C3D3273F1B4C86252DA11BBDC9568773DA0C"
    },
    @{
        Path = "Stable-diffusion\cyberrealistic_final.safetensors"
        Sha256 = "2209C07B331A06CB28CF7C830EC758AE5B49EB97FAB21F5DE6B18C7BE8B41554"
    },
    @{
        Path = "ControlNet\control_v11p_sd15_openpose.pth"
        Sha256 = "DB97BECD92CD19AFF71352A60E93C2508DECBA3DEE64F01F686727B9B406A9DD"
    }
)

foreach ($expected in $expectedFiles) {
    $path = Join-Path $modelRoot $expected.Path
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing image model: $path"
    }
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actualHash -ne $expected.Sha256) {
        throw "SHA256 mismatch: $($expected.Path)"
    }
    Write-Output "HASH_OK $($expected.Path)"
}

$baseUrl = "http://127.0.0.1:7861"
$options = Invoke-RestMethod -Uri "$baseUrl/sdapi/v1/options" -TimeoutSec 10
$scripts = Invoke-RestMethod -Uri "$baseUrl/sdapi/v1/scripts" -TimeoutSec 10
$controlModels = Invoke-RestMethod -Uri "$baseUrl/controlnet/model_list" -TimeoutSec 10
$controlModules = Invoke-RestMethod -Uri "$baseUrl/controlnet/module_list" -TimeoutSec 10

if ($scripts.txt2img -notcontains "ADetailer") {
    throw "ADetailer is not loaded by Forge."
}
if (-not ($controlModels.model_list -match "control_v11p_sd15_openpose")) {
    throw "The OpenPose ControlNet model is not loaded by Forge."
}
if ($controlModules.module_list -notcontains "openpose_full") {
    throw "The openpose_full ControlNet preprocessor is unavailable."
}

$listeners = netstat -ano | Select-String "127\.0\.0\.1:7861\s+.*LISTENING"
if (-not $listeners) {
    throw "Forge is not restricted to the 127.0.0.1:7861 listener."
}

$rembgModel = Join-Path $repoRoot "workspace\rembg\models\birefnet-general.onnx"
if (-not (Test-Path -LiteralPath $rembgModel)) {
    throw "The BiRefNet-General rembg model is missing."
}

Write-Output "FORGE_OK $($options.sd_model_checkpoint)"
Write-Output "ADETAILER_OK"
Write-Output "CONTROLNET_OK openpose_full"
Write-Output "REMBG_OK birefnet-general"
