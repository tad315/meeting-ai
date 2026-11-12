param([int]$Port = 8009, [switch]$LocalAssistant)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    throw 'Create a Python 3.12 virtual environment: python -m venv .venv'
}
$ffmpeg = Join-Path (Split-Path $PSScriptRoot -Parent) 'frontend/src-tauri/binaries/ffmpeg-x86_64-pc-windows-msvc.exe'
if (-not $env:FFMPEG_PATH -and (Test-Path $ffmpeg)) { $env:FFMPEG_PATH = $ffmpeg }
if ($LocalAssistant -and (Test-Path (Join-Path $PSScriptRoot 'data\ollama-runtime\ollama.exe'))) {
    try { & (Join-Path $PSScriptRoot 'setup-summary.ps1') }
    catch { Write-Warning "Local summary service could not start: $_" }
}
# One port for everything: the app and the Kaggle worker API (/gpu).
$env:VOXMEET_PORT = "$Port"
Write-Host "VoxMeet: http://127.0.0.1:$Port"
& .venv/Scripts/python.exe -m uvicorn app.step_one:app --host 127.0.0.1 --port $Port
