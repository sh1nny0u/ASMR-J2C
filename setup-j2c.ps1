# setup-j2c.ps1 - 可靠安装 ASMR-J2C 后端依赖
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Setting up ASMR-J2C environment ===" -ForegroundColor Cyan

# 1. 查找 Python 3.10/3.11
$pythonExe = $null
foreach ($try in @("py -3.11", "py -3.10", "python")) {
    try {
        $ver = & $try -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -ge "3.10") {
            $pythonExe = $try
            Write-Host "Found Python $ver via $try" -ForegroundColor Green
            break
        }
    } catch {}
}
if (-not $pythonExe) {
    Write-Host "ERROR: Python 3.10 or 3.11 not found. Please install Python." -ForegroundColor Red
    exit 1
}

# 2. 删除旧虚拟环境（如果存在）
if (Test-Path ".venv") {
    Write-Host "Removing existing .venv..." -ForegroundColor Yellow
    Remove-Item ".venv" -Recurse -Force
}

# 3. 创建全新虚拟环境
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
& $pythonExe -m venv .venv
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
    exit 1
}

# 4. 升级 pip
Write-Host "Upgrading pip..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --no-cache-dir

# 5. 安装核心依赖（强制重装 gradio_client）
Write-Host "Installing dependencies: fastapi, uvicorn, httpx, python-multipart, gradio_client" -ForegroundColor Yellow
& $venvPython -m pip install fastapi uvicorn[standard] httpx python-multipart --no-cache-dir
& $venvPython -m pip install gradio_client --no-cache-dir --force-reinstall

# 6. 验证 gradio_client 可导入
Write-Host "Verifying gradio_client..." -ForegroundColor Yellow
$importTest = & $venvPython -c "import gradio_client; print('OK')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: gradio_client import failed. Output: $importTest" -ForegroundColor Red
    exit 1
}
Write-Host "gradio_client verified." -ForegroundColor Green

# 7. 验证 app.main 可导入
Write-Host "Verifying app.main..." -ForegroundColor Yellow
& $venvPython -c "import sys; sys.path.insert(0, '.'); from app.main import app" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: app.main import failed." -ForegroundColor Red
    exit 1
}

Write-Host "ASMR-J2C environment ready." -ForegroundColor Green
