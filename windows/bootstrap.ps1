# Laziest entry point. Installs Git + Python (via winget) if missing, clones the
# repo to C:\crypto_test, then runs setup.ps1. Run in PowerShell:
#
#   irm https://raw.githubusercontent.com/Durant0509/crypto_test/main/windows/bootstrap.ps1 | iex
#
# ASCII-only on purpose (see note in setup.ps1).
$ErrorActionPreference = "Stop"
$dir = "C:\crypto_test"

function Test-Cmd($n){ [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path","User")
}

if (-not (Test-Cmd git)) {
  Write-Host "Installing Git..." -ForegroundColor Yellow
  winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (-not (Test-Cmd python)) {
  Write-Host "Installing Python..." -ForegroundColor Yellow
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (-not (Test-Cmd git)) {
  throw "Git was installed but this window can't see it yet. Close PowerShell, open a NEW one, and paste the same command again."
}

if (Test-Path $dir) {
  Write-Host "$dir exists, pulling latest..." -ForegroundColor Cyan
  git -C $dir pull
} else {
  Write-Host "Cloning to $dir ..." -ForegroundColor Cyan
  git clone https://github.com/Durant0509/crypto_test.git $dir
}
Set-Location $dir
powershell -NoProfile -ExecutionPolicy Bypass -File "$dir\windows\setup.ps1"
