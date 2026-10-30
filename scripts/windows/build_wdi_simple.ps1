# Build wdi-simple.exe (libwdi) for silent WinUSB/libusb0 installs.
# Requires Visual Studio Build Tools + msbuild on the runner.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$OutDir = Join-Path $Root "wattbike_logger\resources\windows"
$Src = Join-Path $Root "build\libwdi-src"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build") | Out-Null

if (Test-Path (Join-Path $OutDir "wdi-simple.exe")) {
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

# Prerequisites next to libwdi (embedder paths)
$WdkMsi = Join-Path $Root "build\wdk-redist.msi"
curl.exe -fsSL --retry 5 -o $WdkMsi "https://go.microsoft.com/fwlink/p/?LinkID=253170"
msiexec /a $WdkMsi /qn TARGETDIR="$Src\wdk"

$Libusb0Zip = Join-Path $Root "build\libusb0.zip"
curl.exe -fsSL --retry 5 -o $Libusb0Zip "https://github.com/mcuee/libusb-win32/releases/download/release_1.4.0.2/libusb-win32-bin-1.4.0.2.zip"
Expand-Archive -Path $Libusb0Zip -DestinationPath $Src -Force
$extracted0 = Get-ChildItem $Src -Directory -Filter "libusb-win32-bin-*" | Select-Object -First 1
if ($extracted0) {
  Rename-Item $extracted0.FullName (Join-Path $Src "libusb0")
}

$Libusbk = Join-Path $Root "build\libusbk.7z"
curl.exe -fsSL --retry 5 -o $Libusbk "https://github.com/mcuee/libusbk/releases/download/V3.1.0.0/libusbK-3.1.0.0-bin.7z"
$seven = Get-Command 7z -ErrorAction SilentlyContinue
if (-not $seven) { choco install 7zip.portable -y --no-progress | Out-Null }
& 7z x $Libusbk "-o$Src" -y | Out-Null
$extractedK = Get-ChildItem $Src -Directory -Filter "libusbK-*-bin" | Select-Object -First 1
if ($extractedK) {
  Rename-Item $extractedK.FullName (Join-Path $Src "libusbk")
}

Push-Location $Src
$toolset = & "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" `
  -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
$msbuild = Join-Path $toolset "MSBuild\Current\Bin\MSBuild.exe"
if (-not (Test-Path $msbuild)) { throw "MSBuild not found" }

& $msbuild libwdi.sln /m `
  /p:Configuration=Release `
  /p:Platform=x64 `
  "/p:BuildMacros=WDK_DIR=\`"../wdk/Windows Kits/8.0\`";LIBUSB0_DIR=\`"../libusb0\`";LIBUSBK_DIR=\`"../libusbk/bin\`""
if ($LASTEXITCODE -ne 0) { throw "msbuild libwdi failed: $LASTEXITCODE" }

$built = @(
  (Join-Path $Src "x64\Release\examples\wdi-simple.exe"),
  (Join-Path $Src "examples\x64\Release\wdi-simple.exe"),
  (Join-Path $Src "Win32\Release\examples\wdi-simple.exe")
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $built) {
  Get-ChildItem $Src -Recurse -Filter wdi-simple.exe | Select-Object -First 5 FullName
  throw "wdi-simple.exe not found after build"
}
Copy-Item $built (Join-Path $OutDir "wdi-simple.exe") -Force
Write-Host "Copied wdi-simple.exe -> $OutDir"
Pop-Location
