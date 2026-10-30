# Build wdi-simple.exe (libwdi) for silent WinUSB/libusb0 installs.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OutDir = Join-Path $Root "wattbike_logger\resources\windows"
$Src = Join-Path $Root "build\libwdi-src"
$DestExe = Join-Path $OutDir "wdi-simple.exe"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build") | Out-Null

if (Test-Path $DestExe) {
  Write-Host "wdi-simple.exe already present"
  exit 0
}

if (Test-Path $Src) { Remove-Item -Recurse -Force $Src }
git clone --depth=1 https://github.com/pbatard/libwdi.git $Src

Get-ChildItem -Recurse $Src -Filter *.vcxproj | ForEach-Object {
  (Get-Content $_.FullName) -replace "v14\d", '$(DefaultPlatformToolset)' |
    Set-Content $_.FullName
}

# WDK redistributable
$WdkMsi = Join-Path $Root "build\wdk-redist.msi"
curl.exe -fsSL --retry 5 -o $WdkMsi "https://go.microsoft.com/fwlink/p/?LinkID=253170"
$WdkTarget = Join-Path $Src "wdk"
New-Item -ItemType Directory -Force -Path $WdkTarget | Out-Null
msiexec /a $WdkMsi /qn "TARGETDIR=$WdkTarget"
Start-Sleep -Seconds 2
$WdkDir = $null
Get-ChildItem -Path $WdkTarget -Recurse -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Name -eq "8.0" -and $_.FullName -match "Windows Kits") { $WdkDir = $_.FullName }
}
if (-not $WdkDir) { $WdkDir = Join-Path $WdkTarget "Windows Kits\8.0" }
if (-not (Test-Path $WdkDir)) {
  Write-Host "WDK contents:"
  Get-ChildItem $WdkTarget -Recurse -Depth 5 | Select-Object -First 50 | ForEach-Object { $_.FullName }
  throw "WDK_DIR not found"
}
Write-Host "WDK_DIR=$WdkDir"

# libusb-win32
$Libusb0Zip = Join-Path $Root "build\libusb0.zip"
curl.exe -fsSL --retry 5 -o $Libusb0Zip "https://github.com/mcuee/libusb-win32/releases/download/release_1.4.0.2/libusb-win32-bin-1.4.0.2.zip"
Expand-Archive -Path $Libusb0Zip -DestinationPath $Src -Force
$extracted0 = Get-ChildItem $Src -Directory -Filter "libusb-win32-bin-*" | Select-Object -First 1
$Libusb0Dir = Join-Path $Src "libusb0"
if ($extracted0) {
  if (Test-Path $Libusb0Dir) { Remove-Item -Recurse -Force $Libusb0Dir }
  Rename-Item $extracted0.FullName $Libusb0Dir
}
Write-Host "LIBUSB0_DIR=$Libusb0Dir"

# libusbK
$LibusbkArchive = Join-Path $Root "build\libusbk.7z"
curl.exe -fsSL --retry 5 -o $LibusbkArchive "https://github.com/mcuee/libusbk/releases/download/V3.1.0.0/libusbK-3.1.0.0-bin.7z"
if (-not (Get-Command 7z -ErrorAction SilentlyContinue)) {
  choco install 7zip.portable -y --no-progress | Out-Null
}
& 7z x $LibusbkArchive "-o$Src" -y | Out-Null
$extractedK = Get-ChildItem $Src -Directory -Filter "libusbK-*-bin" | Select-Object -First 1
$LibusbkDir = Join-Path $Src "libusbk"
if ($extractedK) {
  if (Test-Path $LibusbkDir) { Remove-Item -Recurse -Force $LibusbkDir }
  Rename-Item $extractedK.FullName $LibusbkDir
}
$LibusbkBin = Join-Path $LibusbkDir "bin"
if (-not (Test-Path $LibusbkBin)) { $LibusbkBin = $LibusbkDir }
Write-Host "LIBUSBK_DIR=$LibusbkBin"

function ConvertTo-FwdPath([string]$p) {
  return ($p -replace "\\", "/")
}

# Patch config.h line-by-line (no complex quoting)
$Cfg = Join-Path $Src "msvc\config.h"
$lines = Get-Content $Cfg
$out = foreach ($line in $lines) {
  if ($line -match '^\s*#define WDK_DIR ') {
    '#define WDK_DIR "' + (ConvertTo-FwdPath $WdkDir) + '"'
  } elseif ($line -match '^\s*#define LIBUSB0_DIR ') {
    '#define LIBUSB0_DIR "' + (ConvertTo-FwdPath $Libusb0Dir) + '"'
  } elseif ($line -match '^\s*#define LIBUSBK_DIR ') {
    '#define LIBUSBK_DIR "' + (ConvertTo-FwdPath $LibusbkBin) + '"'
  } else {
    $line
  }
}
$out | Set-Content $Cfg -Encoding ASCII
Write-Host "Patched msvc/config.h"

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vsPath = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
$msbuild = Join-Path $vsPath "MSBuild\Current\Bin\MSBuild.exe"
if (-not (Test-Path $msbuild)) { throw "MSBuild not found" }

Push-Location $Src
& $msbuild "libwdi.sln" "/m" "/p:Configuration=Release" "/p:Platform=x64"
if ($LASTEXITCODE -ne 0) { throw "msbuild libwdi failed: $LASTEXITCODE" }

$built = Get-ChildItem $Src -Recurse -Filter "wdi-simple.exe" |
  Where-Object { $_.FullName -match "Release" } |
  Select-Object -First 1
if (-not $built) {
  Get-ChildItem $Src -Recurse -Filter "wdi-simple.exe" | ForEach-Object { Write-Host $_.FullName }
  throw "wdi-simple.exe not found after build"
}
Copy-Item $built.FullName $DestExe -Force
Write-Host ("Copied {0} to {1}" -f $built.FullName, $DestExe)
Pop-Location
