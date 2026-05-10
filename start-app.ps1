# start-app.ps1 - 稳定启动脚本（自动释放端口，错误时暂停）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ProjectRoot = $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "ASMR-J2C Launcher (gradio_client version)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

function Stop-ProcessOnPorts {
    param([int[]]$Ports)
    foreach ($port in $Ports) {
        try {
            $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
            foreach ($conn in $connections) {
                $pid = $conn.OwningProcess
                if ($pid) {
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                    Write-Host "      Killed process $pid using port $port" -ForegroundColor Yellow
                }
            }
        } catch {
            # 如果无法获取连接信息，忽略
        }
    }
}

# 清理函数（用于脚本退出时）
$indexProcess = $null
$logJob = $null
function Cleanup {
    Write-Host "`nCleaning up..." -ForegroundColor Yellow
    if ($indexProcess -and (-not $indexProcess.HasExited)) {
        Write-Host "Stopping IndexTTS2..." -ForegroundColor Yellow
        $indexProcess.Kill()
        $indexProcess.WaitForExit(5000) | Out-Null
        Write-Host "IndexTTS2 stopped." -ForegroundColor Green
    }
    if ($logJob) {
        Stop-Job -Job $logJob -ErrorAction SilentlyContinue
        Remove-Job -Job $logJob -ErrorAction SilentlyContinue
    }
}

try {
    # 释放可能占用的端口
    Write-Host "[0/2] Releasing ports 7860 and 7861..." -ForegroundColor Yellow
    Stop-ProcessOnPorts -Ports @(7860, 7861)
    Start-Sleep -Seconds 1

    # 1. 启动 IndexTTS2
    Write-Host "[1/2] Starting IndexTTS2 on port 7860..." -ForegroundColor Yellow

    $indexVenvPython = Join-Path $ProjectRoot "indexTTS2\index-tts\.venv\Scripts\python.exe"
    $webuiPath = Join-Path $ProjectRoot "indexTTS2\index-tts\webui.py"
    $modelDir = Join-Path $ProjectRoot "indexTTS2\index-tts\checkpoints"

    if (-not (Test-Path $indexVenvPython) -or -not (Test-Path $webuiPath)) {
        Write-Host "ERROR: IndexTTS2 environment not ready. Run setup-index.ps1 first." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }

    $indexLog = Join-Path $LogDir "indexTTS2.log"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $indexVenvPython
    $psi.Arguments = "`"$webuiPath`" --port 7860 --host 127.0.0.1 --fp16 --model_dir `"$modelDir`""
    $psi.WorkingDirectory = (Split-Path $webuiPath -Parent)
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $indexProcess = [System.Diagnostics.Process]::Start($psi)

    $logJob = Start-Job -ScriptBlock {
        param($p, $log)
        while (-not $p.HasExited) {
            $out = $p.StandardOutput.ReadLine()
            if ($out) { Add-Content -Path $log -Value $out }
            $err = $p.StandardError.ReadLine()
            if ($err) { Add-Content -Path $log -Value $err }
            Start-Sleep -Milliseconds 200
        }
    } -ArgumentList $indexProcess, $indexLog

    Write-Host "      Waiting for IndexTTS2 (max 300s)..." -ForegroundColor Cyan
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:7860/config" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch { }
        Write-Host "      Still waiting... ($(($i+1)*5)/300s)"
    }
    if (-not $ready) {
        throw "IndexTTS2 failed to start or timeout."
    }
    Write-Host "      IndexTTS2 ready. Opening browser..." -ForegroundColor Green
    Start-Process "http://127.0.0.1:7860"

    # 2. 启动 ASMR-J2C
    Write-Host ""
    Write-Host "[2/2] Starting ASMR-J2C on port 7861..." -ForegroundColor Yellow
    $j2cVenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $j2cVenvPython)) {
        throw "ASMR-J2C virtual environment not found."
    }

    $j2cLog = Join-Path $LogDir "j2c.log"
    Write-Host "      Logging to: $j2cLog" -ForegroundColor Cyan

    Write-Host "      Running pre-flight checks..." -ForegroundColor Cyan
    $pyVersion = & $j2cVenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
    Write-Host "      Python version: $pyVersion" -ForegroundColor Gray
    $gradioClientVersion = & $j2cVenvPython -c "import gradio_client; print(gradio_client.__version__)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      ERROR: gradio_client import failed: $gradioClientVersion" -ForegroundColor Red
        throw "gradio_client not available"
    }
    Write-Host "      gradio_client version: $gradioClientVersion" -ForegroundColor Green
    $importTest = & $j2cVenvPython -c "import sys; sys.path.insert(0, '.'); from app.main import app; print('OK')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      ERROR: app.main import failed: $importTest" -ForegroundColor Red
        throw "Cannot import app.main"
    }
    Write-Host "      app.main imported successfully." -ForegroundColor Green

    # 启动 uvicorn 并保持运行（前台阻塞），同时在后台打开浏览器
    Write-Host "      Starting uvicorn (foreground)..." -ForegroundColor Cyan
    Start-Process "http://127.0.0.1:7861"
    & $j2cVenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 7861 --log-level info
    $uvicornExitCode = $LASTEXITCODE
    if ($uvicornExitCode -ne 0) {
        throw "uvicorn exited with code $uvicornExitCode"
    }

} catch {
    Write-Host "`nERROR: $_" -ForegroundColor Red
    Write-Host "Press Enter to exit..." -ForegroundColor Yellow
    Read-Host
    exit 1
} finally {
    Cleanup
}

