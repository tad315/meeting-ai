param([switch]$Install)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'data\ollama-runtime'
$env:OLLAMA_MODELS = Join-Path $PSScriptRoot 'data\ollama-models'
$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_NUM_PARALLEL = '1'
New-Item -ItemType Directory -Force $runtime,$env:OLLAMA_MODELS | Out-Null
$exe = Join-Path $runtime 'ollama.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    if (-not $Install) { throw 'Run .\setup-summary.ps1 -Install once to download the local runtime and model to this project on D.' }
    $archive = Join-Path $runtime 'ollama.zip'
    Write-Host 'Downloading Ollama portable to project data (several GB)...'
    & curl.exe -L --fail --retry 3 'https://ollama.com/download/ollama-windows-amd64.zip' -o $archive
    if ($LASTEXITCODE -ne 0) { throw 'Ollama download failed. Run this command again to retry.' }
    Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
    Remove-Item -LiteralPath $archive
}
try { $null = Invoke-RestMethod 'http://127.0.0.1:11435/api/tags' -TimeoutSec 3 }
catch {
    Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'server.log') -RedirectStandardError (Join-Path $runtime 'server-error.log')
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod 'http://127.0.0.1:11435/api/tags' -TimeoutSec 2; $ready=$true; break } catch {}
    }
    if (-not $ready) { throw 'Ollama could not start. See data\ollama-runtime\server-error.log.' }
}
$tags = Invoke-RestMethod 'http://127.0.0.1:11435/api/tags' -TimeoutSec 5
if ('qwen2.5:3b' -notin @($tags.models.name)) {
    if (-not $Install) { throw 'Model is missing. Run setup-summary.ps1 -Install once.' }
    & $exe pull qwen2.5:3b
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Run setup-summary.ps1 again.' }
}
Write-Host 'Ollama ready. Set OLLAMA_URL=http://127.0.0.1:11435 and OLLAMA_MODEL=qwen2.5:3b in .env; restart run.ps1.'
