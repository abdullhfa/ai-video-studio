@echo off
title Auto Sync GitHub - youtube-create
cd /d "%~dp0"
echo Auto-sync to https://github.com/abdullhfa/youtube-create
echo Press Ctrl+C to stop.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\auto-sync-github.ps1"
pause
