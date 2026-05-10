@echo off
title ASMR-J2C Environment Setup
echo ========================================
echo Installing ASMR-J2C backend dependencies...
echo ========================================
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0setup-j2c.ps1"
if %errorlevel% neq 0 (
    echo.
    echo Setup failed with error code %errorlevel%.
    pause
    exit /b %errorlevel%
)
echo.
echo Setup completed successfully.
pause
