# Install-WattbikeLogger.ps1 — copia l'exe, sblocca SmartScreen zone, scorciatoia Start
# Esegui con: tasto destro → "Esegui con PowerShell" (oppure: powershell -ExecutionPolicy Bypass -File .\Install-WattbikeLogger.ps1)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms | Out-Null

function Show-Msg([string]$Text, [string]$Title = "Wattbike Logger") {
    [System.Windows.Forms.MessageBox]::Show($Text, $Title) | Out-Null
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe = Get-ChildItem -Path $Root -Filter "WattbikeLogger*.exe" | Select-Object -First 1
if (-not $Exe) {
    Show-Msg "WattbikeLogger-windows-x64.exe non trovato nella cartella dello zip."
    exit 1
}

$DestDir = Join-Path $env:LOCALAPPDATA "WattbikeLogger"
New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
$DestExe = Join-Path $DestDir "WattbikeLogger.exe"
Copy-Item -Force $Exe.FullName $DestExe
Unblock-File -Path $DestExe -ErrorAction SilentlyContinue

$Wsh = New-Object -ComObject WScript.Shell
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Shortcut = $Wsh.CreateShortcut((Join-Path $StartMenu "Wattbike Logger.lnk"))
$Shortcut.TargetPath = $DestExe
$Shortcut.WorkingDirectory = $DestDir
$Shortcut.Save()

Start-Process $DestExe
Show-Msg "Installazione completata.`n$DestExe`nScorciatoia: menu Start → Wattbike Logger."
