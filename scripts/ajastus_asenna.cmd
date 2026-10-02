@echo off
REM Asentaa ajastetun ajon (ti-la klo 10). Poisto: ajastus_poista.cmd
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ajastus_asenna.ps1"
pause
