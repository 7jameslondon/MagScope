$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error "Missing repository virtual environment: $venvPython. Create or link .venv before running tests."
}

& $venvPython -m pytest @args
