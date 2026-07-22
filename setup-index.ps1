$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$indexDir = Join-Path $PSScriptRoot "indexTTS2\index-tts"
$venvPath = Join-Path $indexDir ".venv"
$modelPath = Join-Path $indexDir "checkpoints"
Write-Host "=== Configure IndexTTS2 ===" -ForegroundColor Cyan
Write-Host "Virtual environment: $venvPath" -ForegroundColor Yellow
Write-Host "Model download location: $modelPath" -ForegroundColor Yellow

if (-not (Test-Path (Join-Path $indexDir "webui.py"))) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Bundled IndexTTS2 source is missing and Git is unavailable."
    }
    $env:GIT_LFS_SKIP_SMUDGE = "1"
    git clone https://github.com/index-tts/index-tts.git $indexDir
} else {
    Write-Host "Using bundled IndexTTS2 source; git pull is skipped." -ForegroundColor Green
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "Installing uv to the current user's uv location." -ForegroundColor Yellow
    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
}

Push-Location $indexDir
try {
    if (-not (Test-Path $venvPath)) {
        uv venv
    } else {
        Write-Host "Reusing existing IndexTTS2 environment: $venvPath" -ForegroundColor Yellow
    }
    uv sync --extra webui
    uv pip uninstall deepspeed
    uv tool install modelscope
    uv run modelscope download --model IndexTeam/IndexTTS-2 --local_dir checkpoints
} finally {
    Pop-Location
}

Write-Host "IndexTTS2 environment and model are ready." -ForegroundColor Green
