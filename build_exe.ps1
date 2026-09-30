# Build and verify a Windows development artifact. No publishing or version bump.
param([string]$Version, [string]$ManifestPath)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'scripts\windows_build_common.ps1')
$EnvironmentBefore = Get-CodexioBuildEnvironment
try {
    Initialize-CodexioWindowsTools -ProjectRoot $ProjectRoot
    $ActualVersion = (Get-Content -LiteralPath (Join-Path $ProjectRoot 'windows\VERSION') -Encoding UTF8 -Raw).Trim()
    if ($ActualVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Invalid windows/VERSION.' }
    if ($Version -and $Version -ne $ActualVersion) { throw 'This command never changes versions. Confirm and update windows/VERSION separately.' }
    $Version = $ActualVersion
    Build-CodexioFrontend -ProjectRoot $ProjectRoot
    $BuildFingerprint = Get-CodexioWindowsFingerprint -ProjectRoot $ProjectRoot
    $Staging = Join-Path $ProjectRoot 'build\staging\windows'
    $Checks = Join-Path $ProjectRoot ("build\checks\v" + $Version)
    foreach ($Directory in @($Staging, $Checks)) { New-Item -ItemType Directory -Path $Directory -Force | Out-Null }
    $CLI = Join-Path $ProjectRoot 'build\cache\tools\wails3.exe'
    $Resource = Join-Path $Staging 'rsrc_windows_amd64.syso'
    & $CLI generate syso -arch amd64 -manifest (Join-Path $ProjectRoot 'windows\build\windows\Codexio.exe.manifest') -icon (Join-Path $ProjectRoot 'src\codexio\icons\app.ico') -info (Join-Path $ProjectRoot 'windows\build\windows\info.json') -out $Resource
    if ($LASTEXITCODE -ne 0) { throw 'Wails resource generation failed.' }
    # Go's Windows linker reads .syso paths directly, so a source snapshot keeps
    # the resource and every generated intermediate under build/staging.
    $CompileRoot = Join-Path $ProjectRoot ('build\staging\windows-source-' + [Guid]::NewGuid().ToString('N'))
    New-CodexioWindowsSourceSnapshot -ProjectRoot $ProjectRoot -Destination $CompileRoot -Resource $Resource
    $StagedExe = Join-Path $Staging 'Codexio.exe'
    Push-Location $CompileRoot
    try {
        & go build -mod=readonly -trimpath -tags production -ldflags '-s -w -H windowsgui' -o $StagedExe .
        if ($LASTEXITCODE -ne 0) { throw 'Go Windows compilation failed.' }
    } finally { Pop-Location }
    $Metadata = (Get-Item -LiteralPath $StagedExe).VersionInfo
    if ($Metadata.ProductVersion -ne $Version -or $Metadata.FileVersion -ne $Version -or $Metadata.ProductName -ne 'Codexio') { throw 'EXE product/file version metadata failed verification.' }
    $Header = [IO.File]::OpenRead($StagedExe)
    try { if ($Header.ReadByte() -ne 77 -or $Header.ReadByte() -ne 90) { throw 'EXE MZ header verification failed.' } } finally { $Header.Dispose() }
    $SmokeOutput = Join-Path $Checks ('smoke-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $SmokeOutput | Out-Null
    $SmokeArguments = @('--mock', '--smoke-test', ('"' + $SmokeOutput + '"'))
    $Process = Start-Process -FilePath $StagedExe -ArgumentList $SmokeArguments -WindowStyle Hidden -PassThru -WorkingDirectory $ProjectRoot
    if (-not $Process.WaitForExit(60000)) { throw "Mock smoke did not exit. Its isolated process/data were preserved at $SmokeOutput; no user application was terminated." }
    $ResultPath = Join-Path $SmokeOutput 'result.json'
    if (-not (Test-Path -LiteralPath $ResultPath)) { throw "Mock smoke produced no result: $SmokeOutput" }
    $Result = Get-Content -LiteralPath $ResultPath -Encoding UTF8 -Raw | ConvertFrom-Json
    if (-not $Result.ok -or @($Result.checks).Count -ne 3 -or $Process.ExitCode -ne 0) { throw "Original three-item smoke failed: $($Result.error)" }
    $ExpectedChecks = '["\u7a0b\u5e8f\u542f\u52a8","\u57fa\u672c\u6570\u636e\u663e\u793a","\u4e3b\u7a97\u53e3\u5173\u95ed\u4e0e\u91cd\u5f00"]' | ConvertFrom-Json
    for ($Index = 0; $Index -lt 3; $Index++) { if ($Result.checks[$Index] -ne $ExpectedChecks[$Index]) { throw 'Smoke check identities differ from the original fixed three checks.' } }
    $Hash = (Get-FileHash -LiteralPath $StagedExe -Algorithm SHA256).Hash.ToLowerInvariant()
    $Size = (Get-Item -LiteralPath $StagedExe).Length
    $StagedManifest = Join-Path $Staging 'latest.json'
    $Manifest = Get-CodexioBaseManifest -ProjectRoot $ProjectRoot -ExplicitPath $ManifestPath -Version $Version
    $Manifest.version = $Version; $Manifest.url = 'https://github.com/Wujuhu/Codexio/releases/download/v' + $Version + '/Codexio.exe'; $Manifest.sha256 = $Hash; $Manifest.size = $Size; $Manifest.notes = 'Codexio ' + $Version
    Write-CodexioJSON -Path $StagedManifest -Value $Manifest
    $Fingerprint = Get-CodexioWindowsFingerprint -ProjectRoot $ProjectRoot
    if ($Fingerprint.sha256 -ne $BuildFingerprint.sha256) { throw 'Sources changed during compilation/smoke; the staged build was retained without delivery.' }
    Write-CodexioJSON -Path (Join-Path $Checks 'source-fingerprint.json') -Value $Fingerprint
    $DevelopmentDir = Join-Path $ProjectRoot 'build\dev\windows'
    $Destination = Join-Path $DevelopmentDir 'Codexio.exe'
    $RunningDestination = @(Get-CimInstance Win32_Process -Filter "Name='Codexio.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($Destination, [StringComparison]::OrdinalIgnoreCase) })
    if ($RunningDestination.Count -gt 0) {
        $DevelopmentDir = Join-Path $DevelopmentDir 'pending'
        New-Item -ItemType Directory -Path $DevelopmentDir -Force | Out-Null
        Copy-Item -LiteralPath $StagedExe -Destination (Join-Path $DevelopmentDir 'Codexio.exe') -Force
        Write-Host 'Running development EXE preserved; verified update delivered to pending.'
    } else {
        & (Join-Path $ProjectRoot 'scripts\publish_exe.ps1') -StagedExe $StagedExe -Version $Version
    }
    Move-Item -LiteralPath $StagedManifest -Destination (Join-Path $DevelopmentDir 'latest.json') -Force
    $DeliveredExe = Join-Path $DevelopmentDir 'Codexio.exe'
    if ((Get-FileHash -LiteralPath $DeliveredExe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) { throw 'Delivered EXE hash changed.' }
    Write-CodexioJSON -Path (Join-Path $Checks 'delivery.json') -Value ([ordered]@{ version=$Version; platform='windows/amd64'; executable=$DeliveredExe; manifest=(Join-Path $DevelopmentDir 'latest.json'); size=$Size; sha256=$Hash; source_sha256=$Fingerprint.sha256; compile_source=$CompileRoot; smoke_result=$ResultPath; smoke_checks=@($Result.checks); runtime='Wails 3.0.0-beta.26'; go_version='1.26.1'; cgo=$false; signed=$false; built_at=[DateTime]::UtcNow.ToString('o') })
    Write-Host "Development build verified: $DeliveredExe"
} finally { Restore-CodexioBuildEnvironment -Values $EnvironmentBefore }
