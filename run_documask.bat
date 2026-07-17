@echo off
cd /d "%~dp0"

REM Try exe first, then python
if exist "DocuMask.exe" (
    start "" "DocuMask.exe"
) else if exist "dist\DocuMask.exe" (
    start "" "dist\DocuMask.exe"
) else (
    set PYTHON=python
    if exist ".venv\Scripts\python.exe" set PYTHON=.venv\Scripts\python.exe
    start "" "%PYTHON%" -m documask.desktop
)