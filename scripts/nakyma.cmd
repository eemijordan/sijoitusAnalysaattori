@echo off
REM Kaynnistaa Streamlit-nakyman selaimeen (localhost:8501).
REM Palvelin elaa vain niin kauan kuin tama ikkuna on auki - sulje ikkuna
REM tai paina Ctrl+C sammuttaaksesi sen.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0nakyma.ps1"
pause
