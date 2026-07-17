<#
.SYNOPSIS
    Build portable DocuMask-Local bundle (no install, just copy to target machine)
.DESCRIPTION
    Creates a portable/ folder with:
      - All source code
      - Virtual environment with all dependencies
      - Models (plain or encrypted)
      - start.bat / stop.bat
      - .env template

    Target machine needs: Python 3.11+ (no admin required)
#>
param(
    [switch]$EncryptModels,
    [string]$OutputDir = "portable"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Portable = Join-Path $ScriptDir $OutputDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " DocuMask-Local Portable Builder" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Clean output
if (Test-Path $Portable) {
    Remove-Item $Portable -Recurse -Force
}
New-Item -ItemType Directory -Path $Portable -Force | Out-Null

# Copy source
Write-Host "[1/5] Copying source code..." -ForegroundColor White
$dirs = @("documask", "models", "tests")
foreach ($d in $dirs) {
    Copy-Item (Join-Path $ScriptDir $d) (Join-Path $Portable $d) -Recurse
}
$files = @("requirements.txt", ".env.example", "README.md", "start.bat", "stop.bat", "install.ps1")
foreach ($f in $files) {
    Copy-Item (Join-Path $ScriptDir $f) $Portable -ErrorAction SilentlyContinue
}

# .env
$envExample = Join-Path $Portable ".env.example"
if (Test-Path $envExample) {
    Copy-Item $envExample (Join-Path $Portable ".env")
}

# Create _work
New-Item -ItemType Directory -Path (Join-Path $Portable "_work") -Force | Out-Null

# Virtual env
Write-Host "[2/5] Creating venv..." -ForegroundColor White
$venv = Join-Path $Portable ".venv"
& python -m venv $venv

# Install deps
Write-Host "[3/5] Installing dependencies..." -ForegroundColor White
$pip = Join-Path $venv "Scripts\pip.exe"
& $pip install --upgrade pip 2>&1 | Out-Null
& $pip install -r (Join-Path $Portable "requirements.txt") 2>&1 | Select-Object -Last 3
Write-Host "       Dependencies installed." -ForegroundColor Green

# Encrypt models (optional)
if ($EncryptModels) {
    Write-Host "[4/5] Encrypting models..." -ForegroundColor White
    $pythonExe = Join-Path $venv "Scripts\python.exe"
    & $pythonExe -c "from documask.crypto_models import encrypt_all_models; from pathlib import Path; encrypt_all_models(Path('models'))" 2>&1
    Write-Host "       Models encrypted." -ForegroundColor Green
}

# Cleanup
Write-Host "[5/5] Cleaning up..." -ForegroundColor White
# Remove __pycache__
Get-ChildItem $Portable -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $Portable -Recurse -File -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue

# Size
$size = (Get-ChildItem $Portable -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " PORTABLE BUILD COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Output : $Portable" -ForegroundColor White
Write-Host "  Size   : $([math]::Round($size, 0)) MB" -ForegroundColor White
Write-Host ""
Write-Host "  To deploy, copy the '$OutputDir' folder to the target machine."
Write-Host "  Target machine must have Python 3.11+ installed."
Write-Host "  Run: start.bat"