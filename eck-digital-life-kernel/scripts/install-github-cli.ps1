param(
    [string]$Version = "2.94.0"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $repoRoot "workspace\tools\gh"
$executable = Join-Path $installRoot "bin\gh.exe"
$archiveName = "gh_${Version}_windows_amd64.zip"
$archive = Join-Path $installRoot "downloads\$archiveName"
$expectedHashes = @{
    "2.94.0" = "c0766af54195dfa0bcd9a0cb63a45c313fbaffdebb9f736f666e9ba4be8c91e8"
}

if (Test-Path -LiteralPath $executable) {
    & $executable --version
    exit $LASTEXITCODE
}
if (-not $expectedHashes.ContainsKey($Version)) {
    throw "No pinned SHA-256 is configured for GitHub CLI $Version."
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $archive) | Out-Null
Invoke-WebRequest `
    -Uri "https://github.com/cli/cli/releases/download/v${Version}/${archiveName}" `
    -OutFile $archive
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actual -ne $expectedHashes[$Version]) {
    throw "GitHub CLI archive failed pinned SHA-256 verification."
}
Expand-Archive -LiteralPath $archive -DestinationPath $installRoot -Force
if (-not (Test-Path -LiteralPath $executable)) {
    throw "GitHub CLI archive did not contain bin/gh.exe."
}
& $executable --version
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI executable verification failed."
}
