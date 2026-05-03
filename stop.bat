@echo off
setlocal

cd /d "%~dp0"

set PORT=7861

echo Stopping ASMR-J2C server on port %PORT%...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT%" ^| findstr "LISTENING"') do set PID=%%a
if defined PID (
    echo Killing process %PID%
    taskkill /PID %PID% /F >nul 2>&1
    echo Done.
) else (
    echo No process found listening on port %PORT%.
)

pause
