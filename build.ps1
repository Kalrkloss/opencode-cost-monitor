<#
.SYNOPSIS
    Builds opencode-tray.exe and optionally an MSI installer.

.DESCRIPTION
    Requires:
      - Python 3.12+ with PyInstaller (pip install pyinstaller)
      - WiX Toolset v3+ (heat.exe, candle.exe, light.exe on PATH) — optional, for MSI
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSCommandPath
$Version = "1.0.0"
$AppName = "opencode-cost-monitor"

# ── Step 0: Generate icon.ico ──
$iconFile = Join-Path $ProjectRoot "icon.ico"
Write-Host "==> Generating icon.ico ..." -ForegroundColor Cyan
python "$ProjectRoot\icon_gen.py" "$iconFile"

# ── Step 1: Build EXE with PyInstaller ──
Write-Host "==> Building EXE with PyInstaller ..." -ForegroundColor Cyan

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Host "Installing PyInstaller ..."
    python -m pip install pyinstaller
}

$distDir = Join-Path $ProjectRoot "dist"
# Use a temp build directory to avoid conflicts with locked files from previous runs
$buildDir = Join-Path $ProjectRoot "build_tmp_dist"
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force -ErrorAction SilentlyContinue }

pyinstaller --noconfirm --onefile `
    --name $AppName `
    --distpath $buildDir `
    --workpath (Join-Path $ProjectRoot "build") `
    --specpath (Join-Path $ProjectRoot "build") `
    --noconsole `
    --add-data "$ProjectRoot\pricing.json;." `
    --add-data "$iconFile;." `
    --icon "$iconFile" `
    "$ProjectRoot\opencode-tray.py"

if (-not $?) { throw "PyInstaller failed" }

# Move to final location — stop previous instance first if running
Get-Process -Name "$AppName" -ErrorAction Ignore | Stop-Process -Force -ErrorAction Ignore
Start-Sleep -Milliseconds 500
New-Item -ItemType Directory -Path $distDir -Force -ErrorAction Ignore | Out-Null
$src = Join-Path $buildDir "$AppName.exe"
$dst = Join-Path $distDir "$AppName.exe"
Copy-Item $src $dst -Force -ErrorAction Ignore
Remove-Item $buildDir -Recurse -Force -ErrorAction Ignore
Remove-Item $buildDir -Recurse -Force -ErrorAction SilentlyContinue

$exePath = Join-Path $distDir "$AppName.exe"
Write-Host "  EXE created: $exePath" -ForegroundColor Green

# ── Step 2: Update _autostart_cmd in the compiled EXE ──
# The EXE's _autostart_cmd() detects sys.frozen and returns exe path.
# No additional steps needed — the code already handles it.

# ── Step 3: Build MSI with WiX (optional) ──
# Detect WiX v3 (candle.exe/light.exe) or v4+ (wix.exe)
$wixExe = Get-Command wix.exe -ErrorAction SilentlyContinue
$candle = Get-Command candle.exe -ErrorAction SilentlyContinue
$light = Get-Command light.exe -ErrorAction SilentlyContinue

if (-not ($wixExe -or ($candle -and $light))) {
    # Check WIX env var or common install dirs for v3 fallback
    $wixDir = $env:WIX
    if (-not $wixDir) {
        $candidates = @(Resolve-Path "${env:ProgramFiles(x86)}\WiX Toolset v*" -ErrorAction Ignore)
        $candidates += @(Resolve-Path "${env:ProgramFiles}\WiX Toolset v*" -ErrorAction Ignore)
        if ($candidates.Count -gt 0) { $wixDir = $candidates[0].Path }
    }
    if ($wixDir) {
        $binDir = Join-Path $wixDir "bin"
        $candle = Get-Command (Join-Path $binDir "candle.exe") -ErrorAction SilentlyContinue
        $light = Get-Command (Join-Path $binDir "light.exe") -ErrorAction SilentlyContinue
    }
}

if ($wixExe -or ($candle -and $light)) {
    Write-Host "==> Building MSI with WiX Toolset ..." -ForegroundColor Cyan

    $msiDir = Join-Path $distDir "msi"
    if (Test-Path $msiDir) { Remove-Item $msiDir -Recurse -Force }
    New-Item -ItemType Directory -Path $msiDir -Force | Out-Null

    $wxsFile = Join-Path $msiDir "installer.wxs"

    # Generate WiX source for the single EXE
    @"
<?xml version='1.0' encoding='utf-8'?>
<Wix xmlns='http://wixtoolset.org/schemas/v4/wxs'>
  <Package Name='OpenCode Cost Monitor' Manufacturer='OpenCode' Version='$Version'
           UpgradeCode='12345678-1234-1234-1234-123456789012'>
    <MajorUpgrade DowngradeErrorMessage='A newer version is already installed.' />
    <Media Id='1' Cabinet='product.cab' EmbedCab='yes' />
    <StandardDirectory Id='ProgramFiles64Folder'>
      <Directory Id='INSTALLDIR' Name='OpenCode Cost Monitor'>
        <Component Id='MainExe' Guid='*'>
          <File Id='OpenCodeCostMonitorExe' Name='$AppName.exe' Source='$exePath' />
        </Component>
      </Directory>
    </StandardDirectory>
    <Feature Id='Complete' Level='1'>
      <ComponentRef Id='MainExe' />
    </Feature>
  </Package>
</Wix>
"@ | Set-Content $wxsFile

    if ($wixExe) {
        # WiX v4+ — use wix build
        $msiFile = Join-Path $msiDir "${AppName}-${Version}.msi"
        & $wixExe build $wxsFile -out $msiFile
        if (-not $?) { throw "wix build failed" }
    } else {
        # WiX v3 — use candle + light
        $wixobjFile = Join-Path $msiDir "installer.wixobj"
        & $candle -arch x64 -out $wixobjFile $wxsFile
        if (-not $?) { throw "candle.exe failed" }
        $msiFile = Join-Path $msiDir "${AppName}-${Version}.msi"
        & $light -out $msiFile $wixobjFile
        if (-not $?) { throw "light.exe failed" }
    }

    Write-Host "  MSI created: $msiFile" -ForegroundColor Green
} else {
    Write-Host "==> Skipping MSI (WiX Toolset not found)" -ForegroundColor Yellow
    Write-Host "  Install WiX Toolset from https://wixtoolset.org to enable MSI builds." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
Write-Host "  EXE: $exePath" -ForegroundColor Green
if ($msiFile) { Write-Host "  MSI: $msiFile" -ForegroundColor Green }


