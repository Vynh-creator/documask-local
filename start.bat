@echo off
title DocuMask-Local
echo ========================================
echo  DocuMask-Local v0.2
echo ========================================
echo.
echo   [1] Desktop GUI (native window)
echo   [2] Web UI (browser on :8501)
echo   [3] Server only (API :8000 + Worker)
echo.
choice /c 123 /n /m "Select mode [1/2/3]: "

cd /d "%~dp0"

REM Auto-detect Python
set PYTHON=python
if exist ".venv\Scripts\python.exe" set PYTHON=.venv\Scripts\python.exe

if errorlevel 3 goto server
if errorlevel 2 goto web
if errorlevel 1 goto desktop

:desktop
echo Starting Desktop GUI...
start "DocuMask Desktop" "%PYTHON%" -m documask.desktop
echo.
echo Desktop app launched in separate window.
goto end

:web
echo Starting Web UI mode...
start "DocuMask API" "%PYTHON%" -m uvicorn documask.api:app --host 127.0.0.1 --port 8000
start "DocuMask Worker" "%PYTHON%" -m documask.worker
start "DocuMask UI" "%PYTHON%" -m streamlit run documask\ui.py --server.port 8501 --server.headless true
echo.
echo  API:  http://localhost:8000
echo  UI:   http://localhost:8501
goto end

:server
echo Starting server only...
start "DocuMask API" "%PYTHON%" -m uvicorn documask.api:app --host 127.0.0.1 --port 8000
start "DocuMask Worker" "%PYTHON%" -m documask.worker
echo.
echo  API:  http://localhost:8000/docs
goto end

:end
echo.
echo Close this window or run stop.bat to stop.
pause