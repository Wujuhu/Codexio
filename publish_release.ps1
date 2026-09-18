# Build and publish a verified stable release. Windows PowerShell 5.1 compatible.
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')]
    [string]$Version,
    [switch]$SkipBuild,
    [switch]$PrepareOnly
)
$ErrorActionPreference = "Stop"
$ReleaseRoot = $PSScriptRoot
$Repository = "Wujuhu/Codexio"
$Tag = "v$Version"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($Part in $Version.Split('.')) {
    if ([long]$Part -gt 65535) { throw "Version numbers must be at most 65535." }
}

function Invoke-ReleaseGh {
    param([string[]]$Arguments)
    $Output = & $script:ReleaseGh @Arguments
    if ($LASTEXITCODE -ne 0) { throw "GitHub command failed: gh $($Arguments -join ' ')" }
    return $Output
}

if (-not $PrepareOnly) {
    $GhCommand = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $GhCommand) {
        throw "Install GitHub CLI first: winget install --id GitHub.cli -e ; reopen PowerShell, then run gh auth login."
    }
    $script:ReleaseGh = $GhCommand.Source
    Invoke-ReleaseGh -Arguments @('auth', 'status', '--hostname', 'github.com') | Out-Host
    $RepoInfo = (Invoke-ReleaseGh -Arguments @('repo','view',$Repository,'--json','isEmpty,isPrivate,defaultBranchRef')) | ConvertFrom-Json
    if ($RepoInfo.isPrivate) { throw "The update repository must be public so clients can download without a token." }
    $Releases = @()
    if (-not $RepoInfo.isEmpty) {
        $Releases = @(Invoke-ReleaseGh -Arguments @('api', "repos/$Repository/releases?per_page=100") | ConvertFrom-Json)
    }
    $Existing = @($Releases | Where-Object { $_.tag_name -eq $Tag })
    if ($Existing.Count -gt 0 -and -not $Existing[0].draft) {
        throw "$Tag is already published. Choose a higher version; published releases are never overwritten."
    }
    foreach ($Release in $Releases) {
        if (-not $Release.draft -and -not $Release.prerelease -and $Release.tag_name -match '^v?([0-9]+\.[0-9]+\.[0-9]+)$') {
            if ([version]$Matches[1] -gt [version]$Version) { throw "A newer release already exists: $($Release.tag_name)" }
        }
    }
}

if (-not $SkipBuild) {
    Push-Location $ReleaseRoot
    try {
        & (Join-Path $ReleaseRoot '.venv\Scripts\python.exe') -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; nothing was published." }
    } finally {
        Pop-Location
    }
    & (Join-Path $ReleaseRoot 'build_exe.ps1') -Version $Version
}
$ReleaseDir = Join-Path (Join-Path $ReleaseRoot 'release') $Version
if (-not (Test-Path -LiteralPath $ReleaseDir)) {
    & (Join-Path $ReleaseRoot '.venv\Scripts\python.exe') (Join-Path $ReleaseRoot 'scripts\prepare_release.py') --version $Version
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare the confirmed release; development builds are preserved." }
}
$Exe = Join-Path $ReleaseDir 'Codexio.exe'
if (-not (Test-Path -LiteralPath $Exe)) { throw "Build output is missing: $Exe" }
if ((Get-Item -LiteralPath $Exe).VersionInfo.ProductVersion -ne $Version) {
    throw "EXE version does not match $Version. Run without -SkipBuild."
}
& (Join-Path $ReleaseRoot '.venv\Scripts\python.exe') (Join-Path $ReleaseRoot 'scripts\verify_release.py') `
    --version $Version --directory $ReleaseDir
if ($LASTEXITCODE -ne 0) { throw "Both platform packages and latest.json must match before publication." }
$UploadDir = Join-Path $ReleaseRoot ('build\staging\upload\' + $Version + '-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $UploadDir -Force | Out-Null
foreach ($Name in @('Codexio.exe', 'Codexio.app.zip', 'latest.json')) {
    Copy-Item -LiteralPath (Join-Path $ReleaseDir $Name) -Destination (Join-Path $UploadDir $Name)
}
$UploadExe = Join-Path $UploadDir 'Codexio.exe'
$UploadApp = Join-Path $UploadDir 'Codexio.app.zip'
$ManifestPath = Join-Path $UploadDir 'latest.json'
$Hash = (Get-FileHash -LiteralPath $UploadExe -Algorithm SHA256).Hash.ToLowerInvariant()
$AppHash = (Get-FileHash -LiteralPath $UploadApp -Algorithm SHA256).Hash.ToLowerInvariant()
$ReleaseNotes = Join-Path $UploadDir 'release-notes.md'
[IO.File]::WriteAllText($ReleaseNotes, '', $Utf8)
if ($PrepareOnly) {
    Write-Host "Prepared: $UploadDir"
    Write-Host "Upload Codexio.exe, Codexio.app.zip and latest.json together to a stable $Tag GitHub release."
    return
}

if ($RepoInfo.isEmpty) { throw "Push the verified source to main before publishing." }

if ($Existing.Count -eq 0) {
    Invoke-ReleaseGh -Arguments @('release', 'create', $Tag, '--repo', $Repository, '--draft', '--target', 'main', '--title', $Tag, '--notes-file', $ReleaseNotes) | Out-Host
} else {
    Invoke-ReleaseGh -Arguments @('release', 'edit', $Tag, '--repo', $Repository, '--draft=true', '--title', $Tag, '--notes-file', $ReleaseNotes) | Out-Host
}
# Upload while still a draft. Only a complete, hash-verified release becomes latest.
$DraftInfo = Invoke-ReleaseGh -Arguments @('api', "repos/$Repository/releases/tags/$Tag") | ConvertFrom-Json
if (-not $DraftInfo.draft) { throw "The release was published elsewhere. Upload stopped." }
Invoke-ReleaseGh -Arguments @('release', 'upload', $Tag, $UploadExe, $UploadApp, $ManifestPath, '--repo', $Repository, '--clobber') | Out-Host
$ManifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$Verified = $false
for ($Attempt = 0; $Attempt -lt 6; $Attempt++) {
    $Uploaded = Invoke-ReleaseGh -Arguments @('api', "repos/$Repository/releases/tags/$Tag") | ConvertFrom-Json
    $ExeAsset = @($Uploaded.assets | Where-Object { $_.name -eq 'Codexio.exe' })
    $AppAsset = @($Uploaded.assets | Where-Object { $_.name -eq 'Codexio.app.zip' })
    $JsonAsset = @($Uploaded.assets | Where-Object { $_.name -eq 'latest.json' })
    if ($Uploaded.assets.Count -eq 3 -and $ExeAsset.Count -eq 1 -and $AppAsset.Count -eq 1 -and $JsonAsset.Count -eq 1 -and
        $ExeAsset[0].digest -eq "sha256:$Hash" -and $AppAsset[0].digest -eq "sha256:$AppHash" -and $JsonAsset[0].digest -eq "sha256:$ManifestHash") {
        $Verified = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $Verified) { throw "Uploaded hash verification failed. Release remains a draft; rerun this command to retry." }
Invoke-ReleaseGh -Arguments @('release', 'edit', $Tag, '--repo', $Repository, '--draft=false', '--latest') | Out-Host
Write-Host "Published: https://github.com/$Repository/releases/tag/$Tag"
Write-Host "Codexio clients will download, verify, install and restart automatically."
