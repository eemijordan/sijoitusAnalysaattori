@echo off
REM Ajaa analysaattorin ilman nakymaa.
REM Ilman parametria: tarkistusajo. Muuten esim. -Ajotyyppi normaali
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0aja.ps1" %*
pause
