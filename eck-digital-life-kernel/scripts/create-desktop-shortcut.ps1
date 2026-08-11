param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "ECK Digital Life Kernel.lnk")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "launch-eck.ps1"
$powershell = (Get-Command powershell.exe).Source
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '"'
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = "Start ECK Digital Life Kernel and open the local dashboard"
$shortcut.IconLocation = $powershell + ',0'
$shortcut.Save()
Write-Output $ShortcutPath
