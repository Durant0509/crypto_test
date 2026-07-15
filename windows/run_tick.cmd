@echo off
REM Launcher the scheduled task calls; just runs the PowerShell tick next to it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0paper_tick.ps1"
