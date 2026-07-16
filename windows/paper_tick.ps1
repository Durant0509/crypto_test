# One hourly paper-trading tick (Windows). Run by the CryptoPaperTick scheduled
# task. Runs BOTH the original single-BTC bot AND the multi-coin experiments
# (ADA/BTC/DOGE tuned), then commits + pushes the dashboard data files.
# Logs to data\paper_tick.log. ASCII-only (PowerShell 5.1 reads .ps1 as ANSI).
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py  = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "data\paper_tick.log"
New-Item -ItemType Directory -Force -Path (Join-Path $repo "data") | Out-Null
$ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
Add-Content $log "=== $ts tick start ==="

# pull FIRST so code changes take effect this tick (not one tick later)
git pull --rebase --autostash 2>&1 | Add-Content $log

# 1) original single-BTC bot (unchanged) -> ledger.json + live.js
& $py "scripts\paper_tick.py" 2>&1 | Add-Content $log
$origOk = ($LASTEXITCODE -eq 0)

# 2) multi-coin experiments (ADA/BTC/DOGE tuned) -> exp_*.json + live_experiments.js
& $py "scripts\paper_tick_experiments.py" 2>&1 | Add-Content $log
$expOk = ($LASTEXITCODE -eq 0)

if ($origOk -or $expOk) {
    git add docs/live.js docs/live_experiments.js paper_state/ 2>&1 | Add-Content $log
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m "paper tick $ts" 2>&1 | Add-Content $log
        git push origin main 2>&1 | Add-Content $log
        Add-Content $log "pushed"
    } else {
        Add-Content $log "no changes"
    }
    if (-not $origOk) { Add-Content $log "WARN: original tick failed (see traceback above)" }
    if (-not $expOk)  { Add-Content $log "WARN: experiments tick failed (see traceback above)" }
} else {
    Add-Content $log "BOTH TICKS FAILED (see traceback above)"
}
Add-Content $log "=== tick done ==="
