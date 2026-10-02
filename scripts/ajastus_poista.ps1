# Poistaa ajastetun ajon Tehtavien ajoituksesta (vaihe 6b). Ajot tehdaan
# sen jalkeen taas vain kasin.

$TehtavanNimi = "Sijoitusanalysaattori"
$Tehtava = Get-ScheduledTask -TaskName $TehtavanNimi -ErrorAction SilentlyContinue
if ($null -eq $Tehtava) {
    Write-Host "Ajastusta ei ole asennettu - ei poistettavaa."
    exit 0
}
Unregister-ScheduledTask -TaskName $TehtavanNimi -Confirm:$false
Write-Host "Ajastus poistettu. Ajot tehdaan taas vain kasin."
