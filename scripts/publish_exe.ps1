# Publish exactly one executable under dist. Compatible with Windows PowerShell 5.1.
param(
    [Parameter(Mandatory = $true)]
    [string]$StagedExe
)
$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DistDir = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "dist"))
$BuildDir = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$DistPrefix = $DistDir.TrimEnd('\') + '\'
$BuildPrefix = $BuildDir.TrimEnd('\') + '\'
$Source = (Resolve-Path -LiteralPath $StagedExe).ProviderPath
if (-not $Source.StartsWith($BuildPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The staged executable must be inside this project's build directory."
}
if ((Get-Item -LiteralPath $Source).Length -eq 0) {
    throw "The staged executable is empty."
}
foreach ($directory in @($DistDir, $BuildDir)) {
    if (Test-Path -LiteralPath $directory) {
        if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to publish through a linked directory: $directory"
        }
    } else {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
}

# Inspect each directory without following junctions or symlinks.
$Files = New-Object 'System.Collections.Generic.List[string]'
$Directories = New-Object 'System.Collections.Generic.List[string]'
$Pending = New-Object 'System.Collections.Generic.Stack[string]'
$Pending.Push($DistDir)
while ($Pending.Count -gt 0) {
    $Current = $Pending.Pop()
    foreach ($item in @(Get-ChildItem -LiteralPath $Current -Force)) {
        $Full = [IO.Path]::GetFullPath($item.FullName)
        if (-not $Full.StartsWith($DistPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unexpected path outside dist: $Full"
        }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to publish through a link inside dist: $Full"
        }
        if ($item.PSIsContainer) {
            $Directories.Add($Full)
            $Pending.Push($Full)
        } elseif ($item.Extension -ieq ".exe") {
            $Files.Add($Full)
        }
    }
}

$Destination = Join-Path $DistDir "Codexio.exe"
$BackupDir = [IO.Path]::GetFullPath((Join-Path $BuildDir ("release-backups\" + [Guid]::NewGuid().ToString("N"))))
if (-not $BackupDir.StartsWith($BuildPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected backup directory."
}
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$ExpectedHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash

$Relocated = New-Object 'System.Collections.Generic.List[object]'
# Retire previous filenames before publishing, so a locked old EXE cannot leave
# two releases in dist. Restore them if any move or the publication fails.
try {
    $Index = 0
    foreach ($file in $Files) {
        if ($file.Equals($Destination, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $Index++
        $Backup = Join-Path $BackupDir ("obsolete-{0}.exe" -f $Index)
        Move-Item -LiteralPath $file -Destination $Backup
        $Relocated.Add(@{ Original = $file; Backup = $Backup })
    }
    if (Test-Path -LiteralPath $Destination) {
        [IO.File]::Replace($Source, $Destination, (Join-Path $BackupDir "previous.exe"))
    } else {
        Move-Item -LiteralPath $Source -Destination $Destination
    }
} catch {
    $PublishError = $_.Exception.Message
    $RestoreErrors = New-Object 'System.Collections.Generic.List[string]'
    for ($Index = $Relocated.Count - 1; $Index -ge 0; $Index--) {
        $Item = $Relocated[$Index]
        try {
            Move-Item -LiteralPath $Item.Backup -Destination $Item.Original
        } catch {
            $RestoreErrors.Add($_.Exception.Message)
        }
    }
    if ($RestoreErrors.Count -gt 0) {
        throw "Publication failed. The staged build is preserved; previous releases needing restoration are in $BackupDir. $PublishError $($RestoreErrors -join ' ')"
    }
    throw "Could not publish dist\Codexio.exe. Close the running widget and retry; the previous release and staged build are preserved. $PublishError"
}
if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $ExpectedHash) {
    throw "Published executable hash verification failed. Backup: $BackupDir"
}

foreach ($directory in ($Directories | Sort-Object Length -Descending)) {
    # Full paths were checked above. Only remove empty, non-linked directories.
    if (@(Get-ChildItem -LiteralPath $directory -Force).Count -eq 0) {
        try {
            Remove-Item -LiteralPath $directory
        } catch {
            # An old process may still use an empty directory as its cwd.
            # It has no executable and does not violate the release invariant.
            Write-Verbose "Empty directory remains in use: $directory"
        }
    }
}
$Remaining = @(Get-ChildItem -LiteralPath $DistDir -File -Filter "*.exe" -Recurse -Force)
if ($Remaining.Count -ne 1 -or $Remaining[0].FullName -ine $Destination) {
    throw "dist still contains more than one executable; publication is incomplete."
}
Write-Host "Published: $Destination"
