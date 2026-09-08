# Launch AIQuota from source.
# ASCII-only so Windows PowerShell 5.x can parse this file without a UTF-8 BOM.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Find-Python {
    foreach ($candidate in @(
            @{ File = "py"; Args = @("-3.13") },
            @{ File = "py"; Args = @("-3.12") },
            @{ File = "py"; Args = @("-3.11") },
            @{ File = "py"; Args = @("-3") },
            @{ File = "python"; Args = @() }
        )) {
        $command = Get-Command $candidate.File -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }
        try {
            & $command.Source @($candidate.Args + "--version") | Out-Null
            return @{ File = $command.Source; Args = $candidate.Args }
        }
        catch {
            continue
        }
    }
    throw "Python not found. Install Python 3.9 or later."
}

$Python = Find-Python
$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating virtual environment..."
    & $Python.File @($Python.Args + @("-m", "venv", $VenvDir))
}

Write-Host "Checking dependencies..."
$Requirements = Join-Path $Root "requirements.txt"
& $VenvPython -m pip install -q -r $Requirements
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip failed; retrying without proxy..."
    $env:NO_PROXY = "*"
    $env:no_proxy = "*"
    python -m pip --python $VenvPython install -q -r $Requirements --proxy ""
}
$env:PYTHONPATH = Join-Path $Root "src"
& $VenvPython -m aiquota @args
