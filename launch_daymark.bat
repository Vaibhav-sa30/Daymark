@echo off
title Daymark

cd /d "%~dp0"

:: Start the daemon — it manages the server AND lives in the system tray
:: If already running, this is a no-op (daemon checks server health itself)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process python -ArgumentList 'daemon.py' -WindowStyle Hidden"

:: Give daemon a moment to spin up the server
powershell -NoProfile -Command "Start-Sleep -Milliseconds 1200"

:: Open Daymark as a standalone app window (no browser chrome)
where msedge >nul 2>&1
if %ERRORLEVEL% equ 0 (
    start "" msedge.exe --app="http://localhost:4173" --window-size=1280,820
    exit /b
)

where chrome >nul 2>&1
if %ERRORLEVEL% equ 0 (
    start "" chrome.exe --app="http://localhost:4173" --window-size=1280,820
    exit /b
)

start http://localhost:4173
