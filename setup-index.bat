@echo off
title IndexTTS2 Environment Setup
echo Installing IndexTTS2 into indexTTS2\index-tts\.venv ...
echo Models will be downloaded to indexTTS2\index-tts\checkpoints ...
powershell -ExecutionPolicy Bypass -File "%~dp0setup-index.ps1"
if %errorlevel% neq 0 (
    echo Setup failed with error code %errorlevel%.
    pause
    exit /b %errorlevel%
)
echo Setup completed successfully.
pause
