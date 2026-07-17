<#
.SYNOPSIS
    DocuMask-Local — Windows installer (fully automated)
.DESCRIPTION
    1. Checks/installs Python 3.11
    2. Creates virtual environment
    3. Installs all dependencies
    4. Copies .env from .env.example
    5. Registers Windows Services (API + Worker)
    6. Creates start/stop shortcuts

    Run as Administrator for service installation.
#>
param(
    [switch]$NoServices,
    [switch]$SkipPython,
    [string]$InstallPath = "C:\ProgramData\DocuMask"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = $null

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " DocuMask-Local Installer v0.1.0" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Check admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin -and -not $NoServices) {
    Write-Host "[WARN] Not running as Admin. Services won't be installed." -ForegroundColor Yellow
    Write-Host "       Re-run as Administrator or use -NoServices flag." -ForegroundColor Yellow
}

# --- Python 3.11 check ---
if (-not $SkipPython) {
    Write-Host "[1/6] Checking Python 3.11..." -ForegroundColor White
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $PythonExe = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
        } catch {
            $PythonExe = $null
        }
    }
    if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) {
        Write-Host "       Python 3.11 not found. Installing with winget..." -ForegroundColor Yellow
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            Write-Host "[FAIL] Install Python 3.11 from https://www.python.org/downloads/" -ForegroundColor Red
            exit 1
        }
        & winget install --id Python.Python.3.11 --exact --scope user --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[FAIL] winget could not install Python 3.11." -ForegroundColor Red
            exit 1
        }
        $PythonExe = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
    }
    if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) {
        Write-Host "[FAIL] Python 3.11 is unavailable after installation." -ForegroundColor Red
        exit 1
    }
    $pyVersion = (& $PythonExe --version 2>&1).Trim()
    Write-Host "       Python OK: $pyVersion" -ForegroundColor Green
} else {
    if (-not $PythonExe) {
        $PythonExe = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
    }
}

# --- Create install directory ---
Write-Host "[2/6] Creating install directory: $InstallPath" -ForegroundColor White
New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null

# --- Copy project files ---
Write-Host "[3/6] Copying DocuMask files..." -ForegroundColor White
$sourceDirs = @("documask", "models", "tests")
foreach ($dir in $sourceDirs) {
    $src = Join-Path $ScriptDir $dir
    $dst = Join-Path $InstallPath $dir
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse
    }
}
$sourceFiles = @("requirements.txt", ".env.example", "README.md", "Dockerfile", "docker-compose.yml")
foreach ($file in $sourceFiles) {
    $src = Join-Path $ScriptDir $file
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $InstallPath $file) -Force
    }
}

# --- Create .env if missing ---
$envFile = Join-Path $InstallPath ".env"
if (-not (Test-Path $envFile)) {
    $envExample = Join-Path $InstallPath ".env.example"
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "       .env created from .env.example" -ForegroundColor Green
    }
}
(Get-Content $envFile) -replace 'DOCUMASK_WORK_DIR=.*', "DOCUMASK_WORK_DIR=$InstallPath\_work" -replace 'DOCUMASK_DB_PATH=.*', "DOCUMASK_DB_PATH=$InstallPath\_work\documask.db" -replace 'DOCUMASK_YOLO_ONNX_PATH=.*', "DOCUMASK_YOLO_ONNX_PATH=$InstallPath\models\stamps_sign.onnx" | Set-Content $envFile
New-Item -ItemType Directory -Path (Join-Path $InstallPath "_work") -Force | Out-Null

# --- Virtual env + deps ---
Write-Host "[4/6] Creating virtual environment..." -ForegroundColor White
$venvPath = Join-Path $InstallPath ".venv"
if (Test-Path $venvPath) {
    Write-Host "       venv already exists, skipping." -ForegroundColor Yellow
} else {
    & $PythonExe -m venv $venvPath
    Write-Host "       venv created." -ForegroundColor Green
}

Write-Host "[5/6] Installing Python dependencies..." -ForegroundColor White
$pipExe = Join-Path $venvPath "Scripts\pip.exe"
& $pipExe install --upgrade pip 2>&1 | Out-Null
& $pipExe install -r (Join-Path $InstallPath "requirements.txt") 2>&1 | Select-Object -Last 5
Write-Host "       Dependencies installed." -ForegroundColor Green

# --- Windows Services ---
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

if (-not $NoServices -and $isAdmin) {
    Write-Host "[6/6] Installing Windows Services..." -ForegroundColor White
    
    # API Service
    $apiName = "DocuMaskAPI"
    $apiBin = "$pythonExe -m uvicorn documask.api:app --host 127.0.0.1 --port 8000"
    $existing = Get-Service $apiName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-Service $apiName -Force -ErrorAction SilentlyContinue
        sc.exe delete $apiName 2>&1 | Out-Null
        Start-Sleep 1
    }
    New-Service -Name $apiName -BinaryPathName "$pythonExe -m uvicorn documask.api:app --host 127.0.0.1 --port 8000" -DisplayName "DocuMask API" -StartupType Automatic -Description "DocuMask-Local REST API on port 8000"
    Start-Service $apiName
    Write-Host "       API service installed and started on :8000" -ForegroundColor Green
    
    # Worker Service
    $workerName = "DocuMaskWorker"
    $existing = Get-Service $workerName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-Service $workerName -Force -ErrorAction SilentlyContinue
        sc.exe delete $workerName 2>&1 | Out-Null
        Start-Sleep 1
    }
    New-Service -Name $workerName -BinaryPathName "$pythonExe -m documask.worker" -DisplayName "DocuMask Worker" -StartupType Automatic -Description "DocuMask-Local background job processor"
    Start-Service $workerName
    Write-Host "       Worker service installed and started." -ForegroundColor Green
} elseif (-not $isAdmin) {
    Write-Host "[6/6] SKIPPED: Windows Services (not Admin)" -ForegroundColor Yellow
    Write-Host "       Run as Administrator for automatic service setup." -ForegroundColor Yellow
}

# --- Create start/stop scripts ---
$startBat = Join-Path $InstallPath "start.bat"
@"
@echo off
echo Starting DocuMask-Local...
cd /d "$InstallPath"
start "DocuMask API" "$pythonExe" -m uvicorn documask.api:app --host 127.0.0.1 --port 8000
start "DocuMask Worker" "$pythonExe" -m documask.worker
start "DocuMask UI" "$pythonExe" -m streamlit run documask\ui.py --server.port 8501
echo.
echo API:  http://localhost:8000
echo UI:   http://localhost:8501
echo.
echo Close this window to stop all services.
pause
"@ | Set-Content $startBat -Encoding ASCII

$stopBat = Join-Path $InstallPath "stop.bat"
@"
@echo off
echo Stopping DocuMask-Local...
taskkill /f /im python.exe 2>nul
echo Done.
"@ | Set-Content $stopBat -Encoding ASCII

$licenseTool = Join-Path $InstallPath "license_tool.bat"
@"
@echo off
cd /d "$InstallPath"
"$pythonExe" -m documask.license
pause
"@ | Set-Content $licenseTool -Encoding ASCII

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " INSTALLATION COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Install path : $InstallPath" -ForegroundColor White
Write-Host "  API          : http://localhost:8000" -ForegroundColor White
Write-Host "  UI           : http://localhost:8501" -ForegroundColor White
Write-Host "  Health check : http://localhost:8000/healthz" -ForegroundColor White
Write-Host ""
Write-Host "  Start all    : $InstallPath\start.bat" -ForegroundColor White
Write-Host "  Stop all     : $InstallPath\stop.bat" -ForegroundColor White
Write-Host "  License tool : $InstallPath\license_tool.bat" -ForegroundColor White
Write-Host ""
Write-Host "  Copy license.key to $InstallPath to activate." -ForegroundColor Yellow
