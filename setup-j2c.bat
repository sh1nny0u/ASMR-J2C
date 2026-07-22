@echo off
title ASMR-J2C Environment Setup
echo Installing ASMR-J2C backend dependencies into .venv ...
powershell -ExecutionPolicy Bypass -File "%~dp0setup-j2c.ps1"
if %errorlevel% neq 0 (
    echo Setup failed with error code %errorlevel%.
    pause
    exit /b %errorlevel%
)
echo Setup completed successfully.
pause
