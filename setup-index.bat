@echo off
title IndexTTS2 Environment Setup
echo ========================================
echo Installing IndexTTS2 (independent environment)...
echo This will take 10-30 minutes depending on network.
echo ========================================
echo.

where git >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: git is not installed or not in PATH.
    echo Please install git from https://git-scm.com/
    pause
    exit /b 1
)

echo git found. Proceeding...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0setup-index.ps1"
if %errorlevel% neq 0 (
    echo.
    echo Setup failed with error code %errorlevel%.
    echo Check logs in setup-index.log if created.
    pause
    exit /b %errorlevel%
)

echo.
echo Setup completed successfully.
echo You can now run start.bat to launch both services.
pause
