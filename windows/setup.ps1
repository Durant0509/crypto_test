# One-shot setup for the crypto_test paper-trading bot on Windows.
# Installs Python if missing, creates the venv + deps, stores your GitHub PAT so
# the hourly push works unattended, runs one tick, and registers the hourly task.
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
  Write-Host "Python 未安裝，用 winget 安裝中..." -ForegroundColor Yellow
  try { winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements } catch {}
  Refresh-Path
}
if (-not (Test-Cmd python)) {
  throw "找不到 python。請手動安裝 Python 3（python.org，安裝時勾選 Add to PATH），關掉 PowerShell 重開，再重跑本腳本。"
}

# --- venv + deps ---
Write-Host "建立 venv 並安裝套件（pandas / numpy / requests ...）..." -ForegroundColor Cyan
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# --- git identity + credentials for unattended push ---
git config user.name  "paper-bot"
git config user.email "actions@users.noreply.github.com"
Write-Host ""
Write-Host "需要一組 GitHub PAT，讓排程能自動 push 結果。" -ForegroundColor Cyan
$pat = Read-Host "貼上 GitHub PAT（會存在本機 $env:USERPROFILE\.git-credentials）"
git config --global --unset-all credential.helper 2>$null
git config --global credential.helper store
"https://Durant0509:$pat@github.com`n" | Out-File -Encoding ascii -NoNewline "$env:USERPROFILE\.git-credentials"

# --- first tick (bootstraps ~3 months of history on first run) ---
Write-Host "跑一次測試 tick（首次會下載一些歷史，稍等 30 秒~1 分）..." -ForegroundColor Cyan
& "$repo\windows\paper_tick.ps1"
Write-Host "---- 最近日誌 ----" -ForegroundColor DarkGray
Get-Content (Join-Path $repo "data\paper_tick.log") -Tail 10

# --- register hourly scheduled task ---
Write-Host "註冊每小時排程 CryptoPaperTick..." -ForegroundColor Cyan
$cmd = Join-Path $repo "windows\run_tick.cmd"
schtasks /Create /TN "CryptoPaperTick" /TR "`"$cmd`"" /SC HOURLY /MO 1 /F | Out-Null

Write-Host ""
Write-Host "OK 完成！每小時自動跑一次，結果 push 到 GitHub。" -ForegroundColor Green
Write-Host "  看結果 : https://durant0509.github.io/crypto_test/"
Write-Host "  日誌   : $repo\data\paper_tick.log"
Write-Host "  停止   : schtasks /Delete /TN CryptoPaperTick /F"
Write-Host "  手動跑 : powershell -ExecutionPolicy Bypass -File windows\paper_tick.ps1"
Write-Host ""
Write-Host "注意：此排程在『使用者登入時』執行，所以請讓這台機器保持登入狀態（螢幕可鎖）。" -ForegroundColor Yellow
