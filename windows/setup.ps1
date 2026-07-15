# One-shot setup for the crypto_test paper-trading bot on Windows.
# Installs Python if missing, creates the venv + deps, stores your GitHub PAT so
# the hourly push works unattended, runs one tick, and registers the hourly task.
# NOTE: kept ASCII-only on purpose -- Windows PowerShell 5.1 reads .ps1 files in
# the system ANSI codepage, so non-ASCII here would be mojibake and fail to parse.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
Write-Host "== crypto_test paper-trading setup ==" -ForegroundColor Cyan
Write-Host "Repo: $repo"

function Test-Cmd($n){ [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path","User")
}

# --- Python ---
if (-not (Test-Cmd python)) {
  Write-Host "Python not found, installing via winget..." -ForegroundColor Yellow
  try { winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements } catch {}
  Refresh-Path
}
if (-not (Test-Cmd python)) {
  throw "python not found. Install Python 3 from python.org (tick 'Add to PATH'), reopen PowerShell, and re-run this script."
}

# --- venv + deps ---
Write-Host "Creating venv and installing packages (pandas / numpy / requests ...)..." -ForegroundColor Cyan
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# --- git identity + credentials for unattended push ---
git config user.name  "paper-bot"
git config user.email "actions@users.noreply.github.com"
Write-Host ""
Write-Host "Need a GitHub PAT so the scheduled task can push results." -ForegroundColor Cyan
$pat = Read-Host "Paste your GitHub PAT (stored locally at $env:USERPROFILE\.git-credentials)"
git config --global --unset-all credential.helper 2>$null
git config --global credential.helper store
"https://Durant0509:$pat@github.com`n" | Out-File -Encoding ascii -NoNewline "$env:USERPROFILE\.git-credentials"

# --- first tick (bootstraps ~3 months of history on first run) ---
Write-Host "Running one test tick (first run downloads some history, ~30-60s)..." -ForegroundColor Cyan
& "$repo\windows\paper_tick.ps1"
Write-Host "---- recent log ----" -ForegroundColor DarkGray
Get-Content (Join-Path $repo "data\paper_tick.log") -Tail 10

# --- register hourly scheduled task ---
Write-Host "Registering hourly scheduled task CryptoPaperTick..." -ForegroundColor Cyan
$cmd = Join-Path $repo "windows\run_tick.cmd"
schtasks /Create /TN "CryptoPaperTick" /TR "`"$cmd`"" /SC HOURLY /MO 1 /F | Out-Null

Write-Host ""
Write-Host "DONE. Runs once an hour and pushes results to GitHub." -ForegroundColor Green
Write-Host "  Dashboard : https://durant0509.github.io/crypto_test/"
Write-Host "  Log       : $repo\data\paper_tick.log"
Write-Host "  Stop      : schtasks /Delete /TN CryptoPaperTick /F"
Write-Host "  Run now   : powershell -ExecutionPolicy Bypass -File windows\paper_tick.ps1"
Write-Host ""
Write-Host "NOTE: the task runs while the user is LOGGED ON, so keep this machine logged in (screen may be locked)." -ForegroundColor Yellow
