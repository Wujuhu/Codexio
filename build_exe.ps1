# Build a standalone AIQuota.exe for Windows.
# ASCII-only so Windows PowerShell 5.x can parse this file without a UTF-8 BOM.
param([string]$Version)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment not found. Run .\run.ps1 once first, or create .venv and install requirements.txt."
}

Write-Host "Installing PyInstaller..."
& $VenvPython -m pip install -q "pyinstaller>=6.3,<7"
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip failed; retrying without proxy..."
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    $env:NO_PROXY = "*"
    $env:no_proxy = "*"
    & $VenvPython -m pip install -q "pyinstaller>=6.3,<7"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install PyInstaller. Check network or proxy, then retry."
    }
}

$Spec = Join-Path $Root "packaging\aiquota.spec"
$Dist = Join-Path $Root "dist"
$Staging = Join-Path $Root "build\release-staging"
$Work = Join-Path $Root "build\pyinstaller"
Write-Host "Building AIQuota.exe..."
& $VenvPython -m PyInstaller --noconfirm --clean --distpath $Staging --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

$StagedExe = Join-Path $Staging "AIQuota.exe"
if (-not (Test-Path -LiteralPath $StagedExe)) {
    throw "Build finished but $StagedExe was not created."
}
if ($Version) {
    & $VenvPython (Join-Path $Root "scripts\set_version.py") $Version
    if ($LASTEXITCODE -ne 0) { throw "Invalid release version." }
}
& (Join-Path $Root "scripts\publish_exe.ps1") -StagedExe $StagedExe
$Exe = Join-Path $Dist "AIQuota.exe"

Write-Host ""
Write-Host "Built: $Exe"
Write-Host "Copy this EXE to another Windows PC. That PC still needs Codex installed and signed in."
Write-Host "Settings are stored per user in %LOCALAPPDATA%\AIQuotaWidget"
