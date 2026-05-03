@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ========================================
echo ASMR-J2C local web launcher
echo ========================================
echo.
echo Log file: "%~dp0start.log"
echo.

set LOG_FILE=%~dp0start.log
> "%LOG_FILE%" echo === ASMR-J2C start %date% %time% ===
>> "%LOG_FILE%" echo Project directory: %~dp0

:: Python detection
set PYTHON_CMD=python
where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.11+ and add to PATH.
    >> "%LOG_FILE%" echo Python not found in PATH.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VER=%%i
echo Python version: %PYTHON_VER%
>> "%LOG_FILE%" echo Python path: %PYTHON_CMD%
>> "%LOG_FILE%" echo Python version: %PYTHON_VER%

:: ffmpeg check
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo ffmpeg not found. Please install ffmpeg and add to PATH.
    >> "%LOG_FILE%" echo ffmpeg not found.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('ffmpeg -version 2^>^&1 ^| findstr /i "ffmpeg version"') do echo %%i
>> "%LOG_FILE%" echo ffmpeg found.

:: Virtual environment
set VENV_DIR=%~dp0.venv
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment...
    >> "%LOG_FILE%" echo Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        >> "%LOG_FILE%" echo Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
    >> "%LOG_FILE%" echo Virtual environment already exists.
)

set VENV_PYTHON=%VENV_DIR%\Scripts\python.exe
set VENV_PIP=%VENV_DIR%\Scripts\pip.exe

echo Installing dependencies...
>> "%LOG_FILE%" echo Installing dependencies...
"%VENV_PIP%" install -r "%~dp0requirements.txt" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo Failed to install dependencies. Check %LOG_FILE%
    pause
    exit /b 1
)

:: Port check
set PORT=7861
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT%" ^| findstr "LISTENING"') do set PID=%%a
if defined PID (
    echo Port %PORT% is already in use by process %PID%.
    echo Stopping it...
    taskkill /PID %PID% /F >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: Start server
echo Starting server at http://127.0.0.1:%PORT%
echo Press Ctrl+C in this window to stop.
>> "%LOG_FILE%" echo Starting server...
start /b "" "%VENV_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo Failed to start server. Check %LOG_FILE%
    pause
    exit /b 1
)

timeout /t 2 /nobreak >nul
start http://127.0.0.1:%PORT%

echo.
echo Server is running. Close this window to stop (or use stop.bat)
echo.
pause >nul
