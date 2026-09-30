# Windows-only build helpers. UTF-8 reads; no installed Python/C++ SDK is needed.
$CodexioBuildEnvironmentNames = @('GOCACHE','GOMODCACHE','GOPATH','GOBIN','GOPROXY','GOTOOLCHAIN','GOTMPDIR','CGO_ENABLED','GOOS','GOARCH','npm_config_cache','npm_config_registry')
function Get-CodexioBuildEnvironment {
    $Values = @{}
    foreach ($Name in $CodexioBuildEnvironmentNames) { $Values[$Name] = [Environment]::GetEnvironmentVariable($Name,'Process') }
    return $Values
}
function Restore-CodexioBuildEnvironment { param([hashtable]$Values)
    foreach ($Name in $CodexioBuildEnvironmentNames) { [Environment]::SetEnvironmentVariable($Name,$Values[$Name],'Process') }
}
function Write-CodexioJSON { param([string]$Path,$Value)
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    $Text = $Value | ConvertTo-Json -Depth 40
    [IO.File]::WriteAllText($Path+'.tmp',$Text+"`n",(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath ($Path+'.tmp') -Destination $Path -Force
}
function Initialize-CodexioWindowsTools { param([string]$ProjectRoot)
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) { throw 'Go 1.26.1 is required.' }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is required.' }
    if ((& go version) -notmatch 'go1\.26\.1 windows/amd64') { throw 'This Windows build requires Go 1.26.1 windows/amd64.' }
    $env:GOCACHE = Join-Path $ProjectRoot 'build\cache\go\build'; $env:GOMODCACHE = Join-Path $ProjectRoot 'build\cache\go\mod'; $env:GOPATH = Join-Path $ProjectRoot 'build\cache\go\path'; $env:GOBIN = Join-Path $ProjectRoot 'build\cache\tools'
    $env:GOPROXY = 'https://goproxy.cn,https://proxy.golang.org,direct'; $env:GOTOOLCHAIN = 'local'; $env:CGO_ENABLED = '0'; $env:GOOS = 'windows'; $env:GOARCH = 'amd64'
    $env:GOTMPDIR=Join-Path $ProjectRoot 'build\staging\go-work'
    $env:npm_config_cache = Join-Path $ProjectRoot 'build\cache\npm'; $env:npm_config_registry = 'https://registry.npmjs.org'
    foreach ($Directory in @($env:GOCACHE,$env:GOMODCACHE,$env:GOPATH,$env:GOBIN,$env:GOTMPDIR,$env:npm_config_cache)) { New-Item -ItemType Directory -Path $Directory -Force | Out-Null }
    $Module = Get-Content -LiteralPath (Join-Path $ProjectRoot 'windows\go.mod') -Encoding UTF8 -Raw
    $Package = Get-Content -LiteralPath (Join-Path $ProjectRoot 'windows\frontend\package.json') -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($Module -notmatch 'github\.com/wailsapp/wails/v3 v3\.0\.0-beta\.26' -or $Package.dependencies.'@wailsio/runtime' -ne '3.0.0-beta.26') { throw 'Wails CLI/Go/frontend must remain pinned to beta.26.' }
    $CLI = Join-Path $env:GOBIN 'wails3.exe'
    if (-not (Test-Path -LiteralPath $CLI)) { & go install github.com/wailsapp/wails/v3/cmd/wails3@v3.0.0-beta.26; if ($LASTEXITCODE -ne 0) { throw 'Pinned Wails CLI preparation failed.' } }
    $CLIOutput = & go version -m $CLI
    if ($LASTEXITCODE -ne 0 -or ($CLIOutput -join "`n") -notmatch 'mod\s+github\.com/wailsapp/wails/v3\s+v3\.0\.0-beta\.26\s') { throw 'Cached Wails CLI build provenance does not match beta.26.' }
}
function Build-CodexioFrontend { param([string]$ProjectRoot)
    Push-Location (Join-Path $ProjectRoot 'windows\frontend')
    try {
        $LockHash=(Get-FileHash -LiteralPath 'package-lock.json' -Algorithm SHA256).Hash
        $LockMarker=Join-Path $ProjectRoot 'build\cache\npm\windows-lock.sha256'
        $CachedHash=''; if (Test-Path -LiteralPath $LockMarker) { $CachedHash=(Get-Content -LiteralPath $LockMarker -Encoding UTF8 -Raw).Trim() }
        if ($CachedHash -ne $LockHash -or -not (Test-Path -LiteralPath 'node_modules\@wailsio\runtime\package.json')) {
            & npm.cmd ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { $env:npm_config_registry='https://registry.npmmirror.com'; & npm.cmd ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' } }
            [IO.File]::WriteAllText($LockMarker,$LockHash,(New-Object Text.UTF8Encoding($false)))
        }
        $Installed = Get-Content -LiteralPath 'node_modules\@wailsio\runtime\package.json' -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($Installed.version -ne '3.0.0-beta.26') { throw 'Installed frontend runtime does not match beta.26.' }
        & npm.cmd run check
        if ($LASTEXITCODE -ne 0) { throw 'Frontend type check failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'build\staging\windows-frontend\index.html'))) { throw 'Production frontend must be emitted under build/staging/windows-frontend.' }
    } finally { Pop-Location }
}
function ConvertTo-CodexioHashtable { param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Management.Automation.PSCustomObject]) { $Result=@{}; foreach ($Property in $Value.PSObject.Properties) { $Result[$Property.Name]=ConvertTo-CodexioHashtable $Property.Value }; return $Result }
    if ($Value -is [System.Collections.IDictionary]) { $Result=@{}; foreach ($Key in $Value.Keys) { $Result[$Key]=ConvertTo-CodexioHashtable $Value[$Key] }; return $Result }
    if ($Value -is [System.Array]) { return ,@($Value | ForEach-Object { ConvertTo-CodexioHashtable $_ }) }
    return $Value
}
function Get-CodexioBaseManifest { param([string]$ProjectRoot,[string]$ExplicitPath,[string]$Version)
    if ($ExplicitPath) { return ConvertTo-CodexioHashtable (Get-Content -LiteralPath $ExplicitPath -Encoding UTF8 -Raw | ConvertFrom-Json) }
    $Roots=@($ProjectRoot)
    Push-Location $ProjectRoot
    try { $Common=(& git rev-parse --git-common-dir 2>$null); if ($LASTEXITCODE -eq 0 -and $Common) { if (-not [IO.Path]::IsPathRooted($Common)) { $Common=Join-Path $ProjectRoot $Common }; $Common=[IO.Path]::GetFullPath($Common); $Original=Split-Path -Parent $Common; if ($Original -ne $ProjectRoot) { $Roots += $Original } } } finally { Pop-Location }
    $Candidates=New-Object 'System.Collections.Generic.List[string]'
    $Candidates.Add((Join-Path $ProjectRoot ('build\checks\v'+$Version+'\manifest-base\latest.json')))
    foreach ($Root in $Roots) { foreach ($Relative in @('build\dev\macos\latest.json',('release\'+$Version+'\latest.json'),'build\dev\windows\latest.json')) { $Candidates.Add((Join-Path $Root $Relative)) }; $Formal=Join-Path $Root 'release'; if (Test-Path -LiteralPath $Formal) { foreach ($Item in @(Get-ChildItem -LiteralPath $Formal -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } | Sort-Object { [Version]$_.Name } -Descending)) { $Candidates.Add((Join-Path $Item.FullName 'latest.json')) } } }
    foreach ($Path in $Candidates) { if (Test-Path -LiteralPath $Path) { $Manifest=ConvertTo-CodexioHashtable (Get-Content -LiteralPath $Path -Encoding UTF8 -Raw | ConvertFrom-Json); if ($Manifest.ContainsKey('macos')) { return $Manifest } } }
    foreach ($Path in $Candidates) { if (Test-Path -LiteralPath $Path) { return ConvertTo-CodexioHashtable (Get-Content -LiteralPath $Path -Encoding UTF8 -Raw | ConvertFrom-Json) } }
    return @{}
}
function New-CodexioWindowsSourceSnapshot { param([string]$ProjectRoot,[string]$Destination,[string]$Resource)
    $StagePrefix=[IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build\staging')).TrimEnd('\')+'\'
    $Destination=[IO.Path]::GetFullPath($Destination)
    if (-not $Destination.StartsWith($StagePrefix,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $Destination)) { throw 'Compile snapshot must be a new directory under build/staging.' }
    New-Item -ItemType Directory -Path $Destination | Out-Null
    $Source=Join-Path $ProjectRoot 'windows'
    foreach ($File in (Get-ChildItem -LiteralPath $Source -File)) { if ($File.Extension -eq '.go' -or $File.Name -in @('VERSION','go.mod','go.sum')) { Copy-Item -LiteralPath $File.FullName -Destination (Join-Path $Destination $File.Name) } }
    Copy-Item -LiteralPath (Join-Path $Source 'backend') -Destination (Join-Path $Destination 'backend') -Recurse
    New-Item -ItemType Directory -Path (Join-Path $Destination 'frontend') | Out-Null
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'build\staging\windows-frontend') -Destination (Join-Path $Destination 'frontend\dist') -Recurse
    if ($Resource) { Copy-Item -LiteralPath $Resource -Destination (Join-Path $Destination 'rsrc_windows_amd64.syso') }
}
function Get-CodexioWindowsFingerprint { param([string]$ProjectRoot)
    $Files=New-Object 'System.Collections.Generic.List[string]'
    foreach ($Relative in @('windows\backend','windows\frontend\src','windows\frontend\public','windows\build')) { $Directory=Join-Path $ProjectRoot $Relative; if (Test-Path -LiteralPath $Directory) { foreach ($File in (Get-ChildItem -LiteralPath $Directory -File -Recurse)) { if ($File.Name -notmatch '_test\.go$') { $Files.Add($File.FullName) } } } }
    foreach ($File in (Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'windows') -File)) { if ($File.Extension -eq '.go' -or $File.Name -in @('VERSION','go.mod','go.sum')) { $Files.Add($File.FullName) } }
    foreach ($Relative in @('windows\frontend\package.json','windows\frontend\package-lock.json','windows\frontend\vite.config.ts','windows\frontend\svelte.config.js','windows\frontend\tsconfig.json','build_exe.ps1','run.ps1','scripts\windows_build_common.ps1','scripts\publish_exe.ps1','src\codexio\icons\app.ico')) { $Files.Add((Join-Path $ProjectRoot $Relative)) }
    $Rows=@(); $Text=New-Object Text.StringBuilder
    foreach ($Path in @($Files | Sort-Object -Unique)) { $Relative=$Path.Substring($ProjectRoot.Length+1).Replace('\','/'); $Hash=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant(); $Rows += @{path=$Relative;sha256=$Hash}; [void]$Text.Append($Relative+':'+$Hash+"`n") }
    $Algorithm=[Security.Cryptography.SHA256]::Create(); try { $Hash=[BitConverter]::ToString($Algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text.ToString()))).Replace('-','').ToLowerInvariant() } finally { $Algorithm.Dispose() }
    return @{algorithm='sha256';sha256=$Hash;files=$Rows}
}
