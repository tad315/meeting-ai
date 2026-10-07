param([switch]$LocalModels)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw 'Install Python 3.12, then run this script again.' }
        & $pythonCommand.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
    }
    & .venv/Scripts/python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    if ($LocalModels) {
        & .venv/Scripts/python.exe -m pip install -r requirements-local.txt
        if ($LASTEXITCODE -ne 0) { throw 'Local speech dependency installation failed.' }
        & .venv/Scripts/python.exe -m app.local_models
        if ($LASTEXITCODE -ne 0) { throw 'Local model download failed. Run this command again to retry.' }
    }
    if (-not (Test-Path '.env')) { Copy-Item .env.example .env }
    Write-Host 'Setup complete. Configure .env, then run: powershell -ExecutionPolicy Bypass -File .\run.ps1'
} finally {
    Pop-Location
}
