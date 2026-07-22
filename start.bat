@echo off
title ASMR-J2C Launcher
echo Starting ASMR-J2C Launcher...
powershell -ExecutionPolicy Bypass -File "%~dp0start-app.ps1"
if %errorlevel% neq 0 (
    echo Script exited with error.
    pause
)
