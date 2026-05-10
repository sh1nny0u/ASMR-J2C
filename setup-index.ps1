# setup-index.ps1 - 部署 IndexTTS2 独立环境
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Setting up IndexTTS2 (independent environment) ===" -ForegroundColor Cyan

$indexDir = "indexTTS2\index-tts"
$indexDirAbs = Join-Path $PSScriptRoot $indexDir

if (-not (Test-Path $indexDirAbs)) {
    Write-Host "Cloning index-tts (skip LFS)..." -ForegroundColor Yellow
    $env:GIT_LFS_SKIP_SMUDGE = "1"
    git clone https://github.com/index-tts/index-tts.git $indexDirAbs
} else {
    Write-Host "Index-tts directory exists. Pulling updates..." -ForegroundColor Yellow
    Push-Location $indexDirAbs
    git pull
    Pop-Location
}

Push-Location $indexDirAbs

# 安装 uv
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
}

Write-Host "Creating virtual environment with uv..." -ForegroundColor Yellow
uv venv
uv sync --extra webui

Write-Host "Removing deepspeed (Windows incompatible)..." -ForegroundColor Yellow
uv pip uninstall deepspeed

Write-Host "Installing modelscope..." -ForegroundColor Yellow
uv tool install modelscope

Write-Host "Downloading model from ModelScope..." -ForegroundColor Yellow
uv run modelscope download --model IndexTeam/IndexTTS-2 --local_dir checkpoints

Pop-Location
Write-Host "IndexTTS2 environment ready." -ForegroundColor Green

