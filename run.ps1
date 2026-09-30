# User-invoked source launch; uses the Go/Wails implementation and embedded assets.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'scripts\windows_build_common.ps1')
$EnvironmentBefore = Get-CodexioBuildEnvironment
try {
    Initialize-CodexioWindowsTools -ProjectRoot $ProjectRoot
    Build-CodexioFrontend -ProjectRoot $ProjectRoot
    $CompileRoot=Join-Path $ProjectRoot ('build\staging\windows-source-'+[Guid]::NewGuid().ToString('N'))
    New-CodexioWindowsSourceSnapshot -ProjectRoot $ProjectRoot -Destination $CompileRoot
    Push-Location $CompileRoot
    try { & go run -mod=readonly -tags production . @args; if ($LASTEXITCODE -ne 0) { throw 'Codexio source process failed.' } } finally { Pop-Location }
} finally { Restore-CodexioBuildEnvironment -Values $EnvironmentBefore }
