# Ajastettu ajo (vaihe 6b). Windowsin Tehtavien ajoitus kaynnistaa taman ti-la
# klo 10 ja, jos kone oli silloin kiinni, kun kone seuraavan kerran avataan.
# Paatoksen siita, ajetaanko mitaan, tekee core/ajastus.py - tama skripti vain
# nayttaa ikkunan, kirjoittaa lokin ja jattaa ikkunan auki Enteriin asti.

$Host.UI.RawUI.WindowTitle = "Sijoitusanalysaattori - ajastettu ajo"

# Repon juuri skriptin omasta sijainnista, kuten aja.ps1:ssa.
$RepoJuuri = Split-Path -Parent $PSScriptRoot
Set-Location $RepoJuuri

$env:PYTHONIOENCODING = "utf-8"

$Python = Join-Path $RepoJuuri ".venv\Scripts\python.exe"
$LokiTiedosto = Join-Path $RepoJuuri "data\ajoloki.txt"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LokiTiedosto) | Out-Null

# EI $ErrorActionPreference = "Stop": sama syy kuin aja.ps1:ssa (Pythonin
# logging kirjoittaa stderriin). Luotetaan paluukoodiin.
# -u: puskuroimaton tuloste, jotta rivit nakyvat ikkunassa heti ja oikeassa
# jarjestyksessa. ForEach-Object { "$_" } muuttaa stderr-rivit tavalliseksi
# tekstiksi, jottei Windows PowerShell nayta jokaista lokirivia punaisena
# virheena.
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - ajastettu ===" |
    Tee-Object -FilePath $LokiTiedosto -Append

# Ikkuna voi aueta kesken muun tyon. Ctrl+C (kopioinnin pikanappain) ei saa
# katkaista ajoa vahingossa, joten se kasitellaan ajon ajan tavallisena
# nappaimena. Ajon voi silti pysayttaa sulkemalla ikkunan.
try { [Console]::TreatControlCAsInput = $true } catch { }
& $Python -u -m core.ajastus 2>&1 | ForEach-Object { "$_" } |
    Tee-Object -FilePath $LokiTiedosto -Append
$Paluukoodi = $LASTEXITCODE
try { [Console]::TreatControlCAsInput = $false } catch { }

Write-Host ""
Write-Host "Valmis (paluukoodi $Paluukoodi). Tulokset nakyvat myos nakymassa: scripts\nakyma.cmd"
# Ajon aikana vahingossa painettu Enter ei saa sulkea ikkunaa ennen kuin
# tuloksen ehtii nahda.
try { $Host.UI.RawUI.FlushInputBuffer() } catch { }
Read-Host "Paina Enter sulkeaksesi ikkunan" | Out-Null
exit $Paluukoodi
