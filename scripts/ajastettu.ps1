# Ajastettu ajo (vaiheet 6b ja 6c). Windowsin Tehtavien ajoitus kaynnistaa
# taman ti-la klo 10, myohastyneena kun kone avataan, ja 2 minuuttia
# kirjautumisen jalkeen. Paatoksen siita, ajetaanko mitaan, tekee
# core/ajastus.py - tama skripti vain nayttaa ikkunan ja kirjoittaa lokin.
# Jos ajo tehtiin tai jokin epaonnistui, ikkuna odottaa Enteria. Jos ajettavaa
# ei ollut, ikkuna nayttaa syyn ja sulkeutuu itse 15 sekunnin kuluttua.

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
# Tee-Object -Variable: rivit talteen, jotta alla voidaan paattaa, tehtiinko
# ajo (core/ajastus.py tulostaa "Ajetaan: ..." vain kun se ajaa jotain).
& $Python -u -m core.ajastus 2>&1 | ForEach-Object { "$_" } |
    Tee-Object -FilePath $LokiTiedosto -Append | Tee-Object -Variable Rivit
$Paluukoodi = $LASTEXITCODE
try { [Console]::TreatControlCAsInput = $false } catch { }

Write-Host ""
$Ajettiin = [bool]($Rivit -match '^Ajetaan:')
if ($Paluukoodi -ne 0 -or $Ajettiin) {
    Write-Host "Valmis (paluukoodi $Paluukoodi). Tulokset nakyvat myos nakymassa: scripts\nakyma.cmd"
    # Ajon aikana vahingossa painettu Enter ei saa sulkea ikkunaa ennen kuin
    # tuloksen ehtii nahda.
    try { $Host.UI.RawUI.FlushInputBuffer() } catch { }
    Read-Host "Paina Enter sulkeaksesi ikkunan" | Out-Null
} else {
    Write-Host "Ei ajettavaa juuri nyt. Ikkuna sulkeutuu 15 sekunnin kuluttua."
    Start-Sleep -Seconds 15
}
exit $Paluukoodi
