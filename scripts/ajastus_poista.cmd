@echo off
REM Poistaa ajastetun ajon. Ajot tehdaan sen jalkeen taas vain kasin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ajastus_poista.ps1"
pause
