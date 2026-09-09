# Kaytto:
#   .\scripts\nakyma.ps1  -> avaa Streamlit-nakyman selaimeen (localhost:8501)
#
# Palvelin elaa vain niin kauan kuin tama ikkuna on auki - se on
# tarkoituksellista, jotta koneelle ei jaa mitaan taustalle pyorimaan.
# Sulje ikkuna (tai Ctrl+C) sammuttaaksesi palvelimen.

# Repon juuri paatellaan skriptin omasta sijainnista, ei kovakoodatusta
# polusta.
$RepoJuuri = Split-Path -Parent $PSScriptRoot
Set-Location $RepoJuuri

$env:PYTHONIOENCODING = "utf-8"

$Streamlit = Join-Path $RepoJuuri ".venv\Scripts\streamlit.exe"
$Sivu = Join-Path $RepoJuuri "dashboard\nakyma.py"

& $Streamlit run $Sivu

exit $LASTEXITCODE
