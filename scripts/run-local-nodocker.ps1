# =============================================================================
# Run the stack WITHOUT Docker (Windows): mock-agent, Gateway, Open WebUI.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run-local-nodocker.ps1        # start
#   powershell -ExecutionPolicy Bypass -File scripts\run-local-nodocker.ps1 -Stop  # stop
#
# One-time setup (Python 3.12 via uv):
#   uv venv --python 3.12 .venv\gateway; uv pip install --python .venv\gateway\Scripts\python.exe -r gateway\requirements.txt
#   uv venv --python 3.12 .venv\webui;   uv pip install --python .venv\webui\Scripts\python.exe open-webui
#
# Differences from docker compose: no Redis (in-memory rate limit), SQLite
# instead of Postgres (data\open-webui), Docker hostnames rewritten to localhost.
# Logs: logs\*.log
# =============================================================================
param([switch]$Stop)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"
$PidFile = Join-Path $Logs "nodocker.pids"

if ($Stop) {
    if (Test-Path $PidFile) {
        foreach ($p in Get-Content $PidFile) {
            try { taskkill /PID $p /T /F *> $null } catch {}
        }
        Remove-Item $PidFile -Force
    }
    "Stopped."
    return
}

New-Item -ItemType Directory -Force $Logs | Out-Null

# --- Load .env into this process (children inherit it) ------------------------
foreach ($line in Get-Content (Join-Path $Root ".env") -Encoding UTF8) {
    $line = $line.Trim([char]0xFEFF).Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
    $k, $v = $line.Split("=", 2)
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process")
}

# --- Docker hostnames -> localhost ---------------------------------------------
Get-ChildItem Env: | Where-Object { $_.Name -like "HERMES_*_BASE_URL" } | ForEach-Object {
    $v = $_.Value -replace "host\.docker\.internal", "localhost" -replace "mock-agent", "localhost"
    [Environment]::SetEnvironmentVariable($_.Name, $v, "Process")
}
$WebuiPort = if ($env:OPENWEBUI_PORT) { $env:OPENWEBUI_PORT } else { "3300" }
$GatewayPort = if ($env:GATEWAY_PORT) { $env:GATEWAY_PORT } else { "8000" }

$env:REDIS_ENABLED = "false"
$env:AGENTS_CONFIG_PATH = Join-Path $Root "config\agents.yaml"
$env:OPENWEBUI_BASE_URL = "http://localhost:$WebuiPort"
$env:OPENAI_API_BASE_URL = "http://localhost:$GatewayPort/v1"
$env:PYTHONUTF8 = "1"

$pids = @()
function Start-Svc($name, $exe, $argList, $cwd) {
    $p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $cwd `
        -RedirectStandardOutput (Join-Path $Logs "$name.log") `
        -RedirectStandardError (Join-Path $Logs "$name.err.log") `
        -WindowStyle Hidden -PassThru
    Write-Host ("{0,-10} pid {1}" -f $name, $p.Id)
    return $p.Id
}

# --- mock-agent :9010 ------------------------------------------------------------
$pids += Start-Svc "mock-agent" (Join-Path $Root ".venv\gateway\Scripts\python.exe") `
    @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "9010") `
    (Join-Path $Root "docker\mock-agent")

# --- Gateway ---------------------------------------------------------------------
$pids += Start-Svc "gateway" (Join-Path $Root ".venv\gateway\Scripts\python.exe") `
    @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", $GatewayPort) `
    (Join-Path $Root "gateway")

# --- Open WebUI (SQLite in data\open-webui) --------------------------------------
$DataDir = Join-Path $Root "data\open-webui"
New-Item -ItemType Directory -Force $DataDir | Out-Null
$env:DATA_DIR = $DataDir
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
$pids += Start-Svc "open-webui" (Join-Path $Root ".venv\webui\Scripts\open-webui.exe") `
    @("serve", "--host", "0.0.0.0", "--port", $WebuiPort) $DataDir

$pids | Set-Content $PidFile
""
"UI:      http://localhost:$WebuiPort   (first start takes a few minutes)"
"Gateway: http://localhost:$GatewayPort/docs"
"Stop:    powershell -ExecutionPolicy Bypass -File scripts\run-local-nodocker.ps1 -Stop"
