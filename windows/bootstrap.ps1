# Laziest entry point. Installs Git + Python (via winget) if missing, clones the
# repo to C:\crypto_test, then runs setup.ps1. Run in PowerShell:
#
#   irm https://raw.githubusercontent.com/Durant0509/crypto_test/main/windows/bootstrap.ps1 | iex
#
$ErrorActionPreference = "Stop"
$dir = "C:\crypto_test"

function Test-Cmd($n){ [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path","User")
}

if (-not (Test-Cmd git)) {
  Write-Host "安裝 Git..." -ForegroundColor Yellow
  winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (-not (Test-Cmd python)) {
  Write-Host "安裝 Python..." -ForegroundColor Yellow
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (-not (Test-Cmd git)) {
  throw "Git 裝好了但這個視窗還抓不到。請關掉 PowerShell、重開一個，再貼一次同樣的指令即可。"
}

if (Test-Path $dir) {
  Write-Host "$dir 已存在，git pull 更新..." -ForegroundColor Cyan
  git -C $dir pull
} else {
  Write-Host "clone 到 $dir ..." -ForegroundColor Cyan
  git clone https://github.com/Durant0509/crypto_test.git $dir
}
Set-Location $dir
powershell -NoProfile -ExecutionPolicy Bypass -File "$dir\windows\setup.ps1"
