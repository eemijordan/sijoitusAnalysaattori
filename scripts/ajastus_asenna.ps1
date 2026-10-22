# Asentaa ajastetun ajon Windowsin Tehtavien ajoitukseen (vaihe 6b).
# Kayttajan saannot: ti-la klo 10; jos kone on silloin kiinni, ajo kun kone
# avataan (core/ajastus.py paattaa onko ajo viela ajankohtainen); kone ei
# koskaan heraa ajoa varten; ajetaan vain kun kayttaja on kirjautuneena;
# ikkuna nakyy aina. Uudelleenajo paivittaa saman tehtavan.
# Poisto: scripts\ajastus_poista.cmd

$ErrorActionPreference = "Stop"

$TehtavanNimi = "Sijoitusanalysaattori"
$RepoJuuri = Split-Path -Parent $PSScriptRoot
$Skripti = Join-Path $RepoJuuri "scripts\ajastettu.ps1"
$Kayttaja = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$Toiminto = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Skripti`"" `
    -WorkingDirectory $RepoJuuri

# Pida samana kuin core/ajastus.py (REVIEW_WEEKDAYS ja SCHEDULED_TIME).
$Ajastin = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
    -DaysOfWeek Tuesday, Wednesday, Thursday, Friday, Saturday -At "10:00"
# New-ScheduledTaskTrigger tallentaa ajan UTC-muodossa (esim.
# 2026-10-02T07:00:00Z), jolloin Tehtavien ajoitus "synkronoi aikavyohykkeiden
# valilla" ja ajo kaynnistyisi talviajassa klo 9.00 - ja core/ajastus.py
# hylkaisi sen "ennen klo 10" -saannolla joka paiva. Paikallinen aika ilman
# Z:aa seuraa kesa- ja talviaikaa. InvariantCulture: suomalainen kulttuuri
# muotoilisi kellonajan pisteilla (10.00.00), jota Tehtavien ajoitus ei ymmarra.
$Ajastin.StartBoundary = (Get-Date -Hour 10 -Minute 0 -Second 0).ToString(
    "yyyy-MM-ddTHH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
# Kirjautumisen jalkeen (vaihe 6c). Jos kone kaynnistetaan vasta klo 10
# jalkeen, Windows yrittaa myohastynytta ajoa jo ennen kirjautumista ja
# luovuttaa, koska tehtava ajetaan vain kirjautuneena (ikkunan takia), eika
# yrita uudelleen. Tama kaynnistin ajaa tehtavan 2 minuuttia kirjautumisen
# jalkeen; core/ajastus.py paattaa edelleen, onko ajo ajankohtainen.
$Kirjautuessa = New-ScheduledTaskTrigger -AtLogOn -User $Kayttaja
$Kirjautuessa.Delay = "PT2M"

# StartWhenAvailable: jos kone oli kiinni klo 10, ajo kaynnistyy kun kone
# avataan. Akkuvirralla ajetaan, koska ajo tehdaan vain kun konetta kaytetaan.
# Parallel: auki unohtunut ikkuna ei esta seuraavan paivan ajoa (ajolukko
# estaa silti kaksi samanaikaista ajoa). 12 h: unohtunut ikkuna suljetaan.
# Ei heratyslippua: kone ei saa koskaan herata itsestaan.
$Asetukset = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances Parallel -ExecutionTimeLimit (New-TimeSpan -Hours 12)

# Interactive: vain kun kayttaja on kirjautuneena, ikkuna nakyy, salasanaa ei
# tallenneta. Limited: ei jarjestelmanvalvojan oikeuksia.
$Kayttajana = New-ScheduledTaskPrincipal -UserId $Kayttaja `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TehtavanNimi -Action $Toiminto `
    -Trigger $Ajastin, $Kirjautuessa -Settings $Asetukset -Principal $Kayttajana `
    -Description "Sijoitusanalysaattorin ajastettu ajo (scripts\ajastettu.ps1)" `
    -Force | Out-Null

# Luetaan takaisin mita Windows oikeasti tallensi.
$Tehtava = Get-ScheduledTask -TaskName $TehtavanNimi
if ($Tehtava.Settings.WakeToRun) {
    Unregister-ScheduledTask -TaskName $TehtavanNimi -Confirm:$false
    throw "Tehtava olisi herattanyt koneen - asennus peruttiin."
}
# Paikallinen klo 10 ilman Z:aa tai aikavyohykepoikkeamaa (+03:00) - kumpikin
# sitoisi ajon kiinteaan UTC-aikaan.
$Viikko = $Tehtava.Triggers |
    Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskWeeklyTrigger" }
$Alku = $Viikko.StartBoundary
if ($Alku -notlike "*T10:00:00") {
    Unregister-ScheduledTask -TaskName $TehtavanNimi -Confirm:$false
    throw "Ajastimen aika tallentui muodossa $Alku eika paikallisena klo 10 - asennus peruttiin."
}
$Kirjautumisessa = $Tehtava.Triggers |
    Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" }
if ($null -eq $Kirjautumisessa) {
    Unregister-ScheduledTask -TaskName $TehtavanNimi -Confirm:$false
    throw "Kirjautumiskaynnistin puuttuu tallennetusta tehtavasta - asennus peruttiin."
}
$Info = Get-ScheduledTaskInfo -TaskName $TehtavanNimi

Write-Host "Ajastus asennettu: $TehtavanNimi"
Write-Host "  Ajot: ti-la klo 10.00 (tarkistus; ti ja pe myos normaaliajo)"
Write-Host "  Kone kiinni klo 10: ajo kun kone avataan, jos New Yorkin porssi ei ole auki"
Write-Host "  Lisaksi 2 min kirjautumisen jalkeen, jos ajo on viela tekematta"
Write-Host "  Herattaa koneen: ei"
Write-Host "  Ajetaan vain kun olet kirjautuneena, ikkuna nakyy aina"
Write-Host "  Seuraava ajo: $($Info.NextRunTime)"
Write-Host "Poisto: scripts\ajastus_poista.cmd"
