$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPath = Join-Path $PSScriptRoot ".venv"
Write-Host "=== Configure ASMR-J2C backend ===" -ForegroundColor Cyan
Write-Host "Install location: $venvPath" -ForegroundColor Yellow

if (Test-Path $venvPath) {
    Write-Host "Existing virtual environment was not changed: $venvPath" -ForegroundColor Yellow
    Write-Host "Run start.bat, or handle that exact directory manually before rebuilding." -ForegroundColor Yellow
    exit 1
}

$pythonCandidates = @(
    @{ Command = "py"; Arguments = @("-3.11") },
    @{ Command = "py"; Arguments = @("-3.10") },
    @{ Command = "python"; Arguments = @() }
)
$python = $null
foreach ($candidate in $pythonCandidates) {
    try {
        $version = & $candidate.Command @($candidate.Arguments) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.10") {
            $python = $candidate
            Write-Host "Using Python $version" -ForegroundColor Green
            break
        }
    } catch { }
}
if (-not $python) {
    throw "Python 3.10 or newer was not found."
}

& $python.Command @($python.Arguments) -m venv $venvPath
$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --no-cache-dir
& $venvPython -m pip install fastapi "uvicorn[standard]" httpx python-multipart gradio_client --no-cache-dir
& $venvPython -c "from app.main import app; print(app.title)"

Write-Host "ASMR-J2C backend environment is ready." -ForegroundColor Green
