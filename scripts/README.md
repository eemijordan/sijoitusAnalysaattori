# scripts/

Käsin ajettavat päiväkomennot. Ajastusta ei ole otettu käyttöön (ks. CLAUDE.md,
"Ajot käynnistetään käsin") — koneella ei haluta pyöriä taustaprosesseja
huomaamatta.

## aja.ps1

```
.\scripts\aja.ps1                      -> tarkistus (paivittainen)
.\scripts\aja.ps1 -Ajotyyppi normaali  -> uusien ehdokkaiden haku
.\scripts\aja.ps1 -Ajotyyppi molemmat  -> tarkistus ensin, sitten normaali
```

## nakyma.ps1

```
.\scripts\nakyma.ps1  -> avaa Streamlit-nakyman selaimeen (localhost:8501)
```

Käynnistää `dashboard/nakyma.py`:n. Palvelin elää vain niin kauan kuin tämä
terminaali-ikkuna on auki — kun ikkuna suljetaan (tai painetaan Ctrl+C),
palvelin sammuu eikä koneelle jää mitään taustalle pyörimään. Tämä on
tarkoituksellista, samasta syystä kuin ajastusta ei ole otettu käyttöön.

## Ajastuksen käyttöönotto myöhemmin

Skripti on kirjoitettu niin, että ajastuksen lisääminen on pelkkä
rekisteröintiaskel — itse skriptiin ei silloin tarvitse koskea. Kun se päivä
tulee:

- Suositeltu rytmi: tarkistus ti–la klo 09:00 Suomen aikaa, normaali ti ja pe
  klo 09:30 Suomen aikaa.
- Ti–la osuu maanantain–perjantain US-pörssin päätöskursseihin kukin kerran
  (ma päätös luetaan ti, ke päätös to, jne.), ja klo 09:00 Suomen aikaa on
  02:00 New Yorkin aikaa — vaiheen 2e `is_bar_ready()`-vartija näkee tuolloin
  jo edellisen päivän lopullisen palkin ja päästää ajon läpi.
- Rekisteröinti tehtäisiin `Register-ScheduledTask`:lla, action
  `powershell.exe -File <repo>\scripts\aja.ps1 -Ajotyyppi <tyyppi>`.
- `New-ScheduledTaskSettingsSet`:lle **on annettava** `-StartWhenAvailable`,
  `-WakeToRun`, `-AllowStartIfOnBatteries` ja `-DontStopIfGoingOnBatteries` —
  oletuksena Windows ei käynnistä ajastettua tehtävää akkuvirralla, ja ilman
  näitä lippuja kannettavalla ajot jäisivät väliin hiljaa, ilman
  virheilmoitusta.

Tätä vaihetta ei ole toteutettu: ei `Register-ScheduledTask`-kutsuja eikä
Task Scheduler -tehtäviä repossa.
