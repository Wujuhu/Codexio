# Build a standalone Codexio.exe for Windows.
# ASCII-only so Windows PowerShell 5.x can parse this file without a UTF-8 BOM.
param([string]$Version)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment not found. Run .\run.ps1 once first, or create .venv and install requirements.txt."
}
if ($Version) {
    & $VenvPython (Join-Path $Root "scripts\set_version.py") $Version
    if ($LASTEXITCODE -ne 0) { throw "Invalid release version." }
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

$Spec = Join-Path $Root "packaging\codexio.spec"
$Dist = Join-Path $Root "dist"
$Staging = Join-Path $Root "build\release-staging"
$Work = Join-Path $Root "build\pyinstaller"
Write-Host "Building Codexio.exe..."
& $VenvPython -m PyInstaller --noconfirm --clean --distpath $Staging --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

$StagedExe = Join-Path $Staging "Codexio.exe"
if (-not (Test-Path -LiteralPath $StagedExe)) {
    throw "Build finished but $StagedExe was not created."
}
& (Join-Path $Root "scripts\publish_exe.ps1") -StagedExe $StagedExe
$Exe = Join-Path $Dist "Codexio.exe"
$ReleaseVersion = (Get-Item -LiteralPath $Exe).VersionInfo.ProductVersion
$Manifest = [ordered]@{
    version = $ReleaseVersion
    url = "https://github.com/Wujuhu/Codexio/releases/download/v$ReleaseVersion/Codexio.exe"
    sha256 = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
    size = (Get-Item -LiteralPath $Exe).Length
    notes = "Codexio $ReleaseVersion"
}
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $Dist "latest.json"), ($Manifest | ConvertTo-Json -Depth 4), $Utf8)

Write-Host ""
Write-Host "Built: $Exe"
Write-Host "Copy this EXE to another Windows PC. That PC still needs Codex installed and signed in."
Write-Host "Existing settings and history are preserved automatically."
