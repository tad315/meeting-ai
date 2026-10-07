$ErrorActionPreference = 'Stop'
$folder = Join-Path $PSScriptRoot 'data\gpu-tools'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$exe = Join-Path $folder 'cloudflared.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    $partial = Join-Path $folder 'cloudflared.part.exe'
    & curl.exe -L --fail --retry 2 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -o $partial
    if ($LASTEXITCODE -ne 0) { throw 'Could not download Cloudflare tunnel utility.' }
    & $partial --version
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel utility validation failed.' }
    Move-Item -LiteralPath $partial -Destination $exe
}
Write-Host 'GPU transfer utility ready. Start the demo and choose Connect this recording to Colab.'
