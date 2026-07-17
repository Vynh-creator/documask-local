@echo off
echo Stopping DocuMask services...
taskkill /f /fi "WINDOWTITLE eq DocuMask*" 2>nul
taskkill /f /im python.exe /fi "MEMUSAGE gt 50000" 2>nul
echo Done.