# Bootstrap local development on Windows (PowerShell)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> Open WebUI Platform — local setup (Windows)"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
} else {
    Write-Host ".env already exists — leaving unchanged"
}

Write-Host "==> Building and starting stack..."
docker compose build
docker compose up -d

$gatewayPort = 8000
if (Test-Path ".env") {
    $line = Get-Content ".env" | Where-Object { $_ -match '^\s*GATEWAY_PORT=' } | Select-Object -First 1
    if ($line -match 'GATEWAY_PORT=(.+)') { $gatewayPort = $Matches[1].Trim() }
}

Write-Host "Waiting for Gateway health on port $gatewayPort ..."
$ok = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$gatewayPort/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}

if (-not $ok) {
    Write-Host "Gateway did not become healthy. Check: docker compose logs gateway"
    exit 1
}

Write-Host ""
Write-Host "Open WebUI:   http://localhost:3000"
Write-Host "Gateway API:  http://localhost:$gatewayPort"
Write-Host "Gateway docs: http://localhost:$gatewayPort/docs"
Write-Host "Done. Create the first admin account in the Open WebUI signup form."
