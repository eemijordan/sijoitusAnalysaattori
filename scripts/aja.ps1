# Kayttö:
#   .\scripts\aja.ps1                      -> tarkistus (paivittainen)
#   .\scripts\aja.ps1 -Ajotyyppi normaali  -> uusien ehdokkaiden haku
#   .\scripts\aja.ps1 -Ajotyyppi molemmat  -> tarkistus ensin, sitten normaali

param(
    [ValidateSet("tarkistus", "normaali", "molemmat")]
    [string]$Ajotyyppi = "tarkistus"
)

# Repon juuri paatellaan skriptin omasta sijainnista, ei kovakoodatusta
# polusta - tama toimii riippumatta mihin repo on kloonattu.
$RepoJuuri = Split-Path -Parent $PSScriptRoot
Set-Location $RepoJuuri

$env:PYTHONIOENCODING = "utf-8"

$Python = Join-Path $RepoJuuri ".venv\Scripts\python.exe"
$LokiTiedosto = Join-Path $RepoJuuri "data\ajoloki.txt"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LokiTiedosto) | Out-Null

# EI $ErrorActionPreference = "Stop": Pythonin logging kirjoittaa oletuksena
# stderriin, joten jokainen INFO-rivi kulkee virhevirtaa pitkin. Yhdistettyna
# uudelleenohjaukseen ja Stop-asetukseen PowerShell voisi tulkita normaalin
# lokituksen keskeyttavaksi virheeksi ja kaataa ajon heti ensimmaisella
# rivilla. Luotetaan paluukoodiin, ei virhevirtaan.

if ($Ajotyyppi -eq "molemmat") {
    # Tarkistus ensin: se voi myyda position ja vapauttaa kateista, joka on
    # silloin saman aamun ostojen kaytettavissa. Toisin pain raha vapautuisi
    # vasta seuraavana paivana.
    "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - tarkistus ===" |
        Tee-Object -FilePath $LokiTiedosto -Append
    & $Python -m core.orkestroija tarkistus 2>&1 |
        Tee-Object -FilePath $LokiTiedosto -Append

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Tarkistus keskeytyi (koodi $LASTEXITCODE) - normaaliajoa ei ajeta."
        exit $LASTEXITCODE
    }

    "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - normaali ===" |
        Tee-Object -FilePath $LokiTiedosto -Append
    & $Python -m core.orkestroija normaali 2>&1 |
        Tee-Object -FilePath $LokiTiedosto -Append

    exit $LASTEXITCODE
}

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $Ajotyyppi ===" |
    Tee-Object -FilePath $LokiTiedosto -Append
& $Python -m core.orkestroija $Ajotyyppi 2>&1 |
    Tee-Object -FilePath $LokiTiedosto -Append

exit $LASTEXITCODE
