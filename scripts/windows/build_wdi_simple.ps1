# Build wdi-simple.exe (libwdi) for silent WinUSB/libusb0 installs.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OutDir = Join-Path $Root "wattbike_logger\resources\windows"
$Src = Join-Path $Root "build\libwdi-src"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build") | Out-Null

$DestExe = Join-Path $OutDir "wdi-simple.exe"
if (Test-Path $DestExe) {
  Write-Host "wdi-simple.exe already present"
  exit 0
}

if (Test-Path $Src) { Remove-Item -Recurse -Force $Src }
git clone --depth=1 https://github.com/pbatard/libwdi.git $Src

# Retarget PlatformToolset to runner default (literal MSBuild macro)
Get-ChildItem -Recurse $Src -Filter *.vcxproj | ForEach-Object {
  (Get-Content $_.FullName) -replace 'v14\d', '$(DefaultPlatformToolset)' |
    Set-Content $_.FullName
}

# --- Prerequisites ---
$WdkMsi = Join-Path $Root "build\wdk-redist.msi"
curl.exe -fsSL --retry 5 -o $WdkMsi "https://go.microsoft.com/fwlink/p/?LinkID=253170"
$WdkTarget = Join-Path $Src "wdk"
New-Item -ItemType Directory -Force -Path $WdkTarget | Out-Null
msiexec /a $WdkMsi /qn "TARGETDIR=$WdkTarget"
# Typical layout after admin install of redistributable
$WdkDir = Get-ChildItem -Path $WdkTarget -Recurse -Directory -Filter "8.0" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "Windows Kits" } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $WdkDir) {
  $WdkDir = Join-Path $WdkTarget "Windows Kits\8.0"
}
if (-not (Test-Path $WdkDir)) {
  Write-Host "WDK layout:"; Get-ChildItem $WdkTarget -Recurse -Depth 4 | Select-Object -First 40 FullName
  throw "WDK_DIR not found under $WdkTarget"
}

$Libusb0Zip = Join-Path $Root "build\libusb0.zip"
curl.exe -fsSL --retry 5 -o $Libusb0Zip "https://github.com/mcuee/libusb-win32/releases/download/release_1.4.0.2/libusb-win32-bin-1.4.0.2.zip"
Expand-Archive -Path $Libusb0Zip -DestinationPath $Src -Force
$extracted0 = Get-ChildItem $Src -Directory -Filter "libusb-win32-bin-*" | Select-Object -First 1
$Libusb0Dir = Join-Path $Src "libusb0"
if ($extracted0) {
  if (Test-Path $Libusb0Dir) { Remove-Item -Recurse -Force $Libusb0Dir }
  Rename-Item $extracted0.FullName $Libusb0Dir
}

$Libusbk = Join-Path $Root "build\libusbk.7z"
curl.exe -fsSL --retry 5 -o $Libusbk "https://github.com/mcuee/libusbk/releases/download/V3.1.0.0/libusbK-3.1.0.0-bin.7z"
if (-not (Get-Command 7z -ErrorAction SilentlyContinue)) {
  choco install 7zip.portable -y --no-progress | Out-Null
}
& 7z x $Libusbk "-o$Src" -y | Out-Null
$extractedK = Get-ChildItem $Src -Directory -Filter "libusbK-*-bin" | Select-Object -First 1
$LibusbkDir = Join-Path $Src "libusbk"
if ($extractedK) {
  if (Test-Path $LibusbkDir) { Remove-Item -Recurse -Force $LibusbkDir }
  Rename-Item $extractedK.FullName $LibusbkDir
}
$LibusbkBin = Join-Path $LibusbkDir "bin"
if (-not (Test-Path $LibusbkBin)) { $LibusbkBin = $LibusbkDir }

# Patch msvc/config.h with absolute forward-slash paths (avoid BuildMacros quoting hell)
function To-Fwd([string]$p) { ($p -replace '\\', '/') }
$Cfg = Join-Path $Src "msvc\config.h"
$cfgText = Get-Content $Cfg -Raw
$cfgText = $cfgText -replace '#define WDK_DIR "[^"]*"', ("#define WDK_DIR `"{0}`"" -f (To-Fwd $WdkDir))
$cfgText = $cfgText -replace '#define LIBUSB0_DIR "[^"]*"', ("#define LIBUSB0_DIR `"{0}`"" -f (To-Fwd $Libusb0Dir))
$cfgText = $cfgText -replace '#define LIBUSBK_DIR "[^"]*"', ("#define LIBUSBK_DIR `"{0}`"" -f (To-Fwd $LibusbkBin))
Set-Content -Path $Cfg -Value $cfgText -NoNewline
Write-Host "Patched config.h:"
Select-String -Path $Cfg -Pattern 'WDK_DIR|LIBUSB0_DIR|LIBUSBK_DIR'

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$toolset = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
$msbuild = Join-Path $toolset "MSBuild\Current\Bin\MSBuild.exe"
if (-not (Test-Path $msbuild)) { throw "MSBuild not found" }

Push-Location $Src
# Build solution; quote each /p separately (no semicolons)
& $msbuild "libwdi.sln" "/m" "/p:Configuration=Release" "/p:Platform=x64" "/t:examples\wdi-simple"
if ($LASTEXITCODE -ne 0) {
  Write-Host "Target examples\wdi-simple failed — building whole solution"
  & $msbuild "libwdi.sln" "/m" "/p:Configuration=Release" "/p:Platform=x64"
}
if ($LASTEXITCODE -ne 0) { throw "msbuild libwdi failed: $LASTEXITCODE" }

$built = Get-ChildItem $Src -Recurse -Filter wdi-simple.exe |
  Where-Object { $_.FullName -match "Release" } |
  Select-Object -First 1
if (-not $built) {
  Get-ChildItem $Src -Recurse -Filter wdi-simple.exe | Select-Object -First 10 FullName
  throw "wdi-simple.exe not found after build"
}
Copy-Item $built.FullName $DestExe -Force
Write-Host "Copied $($built.FullName) -> $DestExe"
Pop-Location
