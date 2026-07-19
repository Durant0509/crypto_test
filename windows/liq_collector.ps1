# Resident liquidation collector (Windows). UNLIKE the hourly paper tick, this
# runs CONTINUOUSLY (a WebSocket must stay connected to receive live liquidation
# events). It is registered as a login-triggered scheduled task that restarts if
# it dies. The Python process itself auto-reconnects on WS drops; this wrapper is
# the outer safety net (restarts if the whole process crashes).
# ASCII-only (PowerShell 5.1 reads .ps1 as ANSI).
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py  = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "data\liq_collector.log"
New-Item -ItemType Directory -Force -Path (Join-Path $repo "data") | Out-Null

while ($true) {
    $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content $log "=== $ts wrapper starting collector ==="
    & $py "-m" "src.live.liq_collector" 2>&1 | Add-Content $log
    Add-Content $log "collector exited; restarting in 15s"
    Start-Sleep -Seconds 15
}
