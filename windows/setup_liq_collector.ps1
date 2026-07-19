# Register the resident liquidation collector as a login-triggered scheduled task
# (CryptoLiqCollector). Runs continuously, restarts on login/crash. Separate from
# the hourly CryptoPaperTick. Run ONCE:
#   powershell -ExecutionPolicy Bypass -File windows\setup_liq_collector.ps1
# ASCII-only (PowerShell 5.1 reads .ps1 as ANSI).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "windows\liq_collector.ps1"
$taskName = "CryptoLiqCollector"

# ensure websocket-client is installed in the venv
$py = Join-Path $repo ".venv\Scripts\python.exe"
Write-Host "Installing websocket-client into venv..."
& $py -m pip install "websocket-client>=1.7"

# build the action: run the resident wrapper hidden
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

# trigger: at logon (the wrapper loops forever, so one start is enough)
$trigger = New-ScheduledTaskTrigger -AtLogOn

# settings: restart if it ever stops, run as long as needed, no timeout
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

# register (remove old one first if present)
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Resident Binance liquidation WS collector"

Write-Host "Registered '$taskName'. Starting it now..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8
Write-Host "`nCheck it's collecting (wait for market volatility):"
Write-Host "  Get-Content data\liq_collector.log -Tail 15"
Write-Host "  dir data\liquidations"
