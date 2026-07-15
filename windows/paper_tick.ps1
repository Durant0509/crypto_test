# One hourly paper-trading tick (Windows). Run by the CryptoPaperTick scheduled
# task. Runs the tick, then commits + pushes docs/live.js + paper_state/ledger.json
# so the dashboard updates. Logs to data\paper_tick.log.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py  = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "data\paper_tick.log"
New-Item -ItemType Directory -Force -Path (Join-Path $repo "data") | Out-Null
$ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
Add-Content $log "=== $ts tick start ==="

& $py "scripts\paper_tick.py" 2>&1 | Add-Content $log
if ($LASTEXITCODE -eq 0) {
    git pull --rebase --autostash 2>&1 | Add-Content $log
    git add docs/live.js paper_state/ledger.json 2>&1 | Add-Content $log
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m "paper tick $ts" 2>&1 | Add-Content $log
        git push origin main 2>&1 | Add-Content $log
        Add-Content $log "pushed"
    } else {
        Add-Content $log "no changes"
    }
} else {
    Add-Content $log "TICK FAILED (see traceback above)"
}
Add-Content $log "=== tick done ==="
