# scripts/

Käsin ajettavat päiväkomennot ja ajastetun ajon skriptit (ks. CLAUDE.md, "Ajot:
käsin tai ajastettuna").

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
tarkoituksellista: koneella ei pyöri mitään huomaamatta.

## Ajastus (vaiheet 6b ja 6c)

```
scripts\ajastus_asenna.cmd  -> asentaa (tai paivittaa) ajastetun ajon
scripts\ajastus_poista.cmd  -> poistaa sen; ajot tehdaan taas vain kasin
```

Asennus rekisteröi Windowsin Tehtävien ajoitukseen tehtävän
`Sijoitusanalysaattori`, joka käynnistää `ajastettu.ps1`:n. Skripti ei päätä
mitään itse: se ajaa `python -m core.ajastus`, joka päättää säännöt (ks.
CLAUDE.md, "Ajolukko ja ajastetun ajon säännöt"), näyttää tulosteen ikkunassa,
ja kirjoittaa sen `data\ajoloki.txt`:hen. Ctrl+C ei katkaise ajoa vahingossa
(se on kopioinnin pikanäppäin); ajon voi pysäyttää sulkemalla ikkunan.

**Ikkuna (vaihe 6c):** jos ajo tehtiin tai jokin epäonnistui (paluukoodi ≠ 0),
ikkuna jää auki Enteriin asti kuten ennenkin. Jos ajettavaa ei ollut, ikkuna
näyttää syyn ja sulkeutuu itse 15 sekunnin kuluttua — muuten jokainen
kirjautuminen jättäisi ruudulle suljettavan ikkunan. Skripti tunnistaa ajon
`core/ajastus.py`:n rivistä `Ajetaan: ...`; `tests/test_skriptit.py` vartioi,
että Python ja skripti käyttävät samaa tunnistetta. Ohituksen paluukoodi on
edelleen 0, joten Tehtävien ajoitus ei näytä ohituspäiviä virheinä.

**Kirjautumiskäynnistin (vaihe 6c).** Torstaina 22.10.2026 ajo jäi tekemättä:
`NumberOfMissedRuns` oli 1, eikä ajolokissa ollut edes ohitusviestiä. Kone ei
ollut päällä klo 10, ja kun se avattiin, Windows yritti `StartWhenAvailable`-
ajoa jo ennen kirjautumista. Tehtävä on `Interactive` (ikkunan takia), joten
Windows luopui ajosta (Tehtävien ajoituksen tapahtuma 332: "user was not
logged on") eikä yrittänyt uudelleen kirjautumisen jälkeen. Aiemmat
myöhästyneet ajot toimivat, koska kone luultavasti heräsi lepotilasta ja
käyttäjä oli jo kirjautuneena. Siksi tehtävällä on toinen käynnistin, joka ajaa
sen 2 minuuttia jokaisen kirjautumisen jälkeen. `core/ajastus.py` hoitaa loput:
ei ennen klo 10:tä, ei pörssin avauduttua, ei kahdesti päivässä eikä su/ma.
**Olemassa oleva asennus päivitetään ajamalla `ajastus_asenna.cmd`
uudelleen** — se korvaa saman tehtävän.

**Käyttäjän säännöt:**

- Tavoiteaika on klo 10 Suomen aikaa: tarkistus ti–la, normaaliajo ti ja pe,
  aina tarkistus ensin.
- Jos kone on klo 10 kiinni, ajo tehdään kun kone avataan, mutta vain jos New
  Yorkin pörssi ei ole vielä auennut. Muuten ajoa ei tehdä, eikä väliin jäänyt
  ajo siirry seuraavalle päivälle.
- Kone ei koskaan herää tai käynnisty itsestään.
- Ajo tehdään vain kun kone on päällä ja käyttäjä kirjautuneena, ja ikkuna
  aukeaa aina näkyviin.

**Asetukset ja miksi:**

| asetus | arvo | miksi |
|---|---|---|
| ajastin | ti–la klo 10.00 | sama kuin `REVIEW_WEEKDAYS` ja `SCHEDULED_TIME` (`core/ajastus.py`) |
| ajastimen aika | paikallinen, ei UTC | `New-ScheduledTaskTrigger` tallentaa ajan UTC-muodossa (`...T07:00:00Z`), jolloin ajo olisi talviajassa klo 9 ja `core/ajastus.py` hylkäisi sen joka päivä; asennus kirjoittaa ajan paikallisena ja tarkistaa tallennetun arvon |
| `-StartWhenAvailable` | päällä | jos kone oli kiinni klo 10, ajo käynnistyy kun kone avataan; `core/ajastus.py` hylkää sen, jos se ei ole enää ajankohtainen |
| kirjautumisen jälkeen | 2 min viiveellä | myöhästynyt ajo ennen kirjautumista hylätään Windowsissa (ks. yllä); asennus tarkistaa, että käynnistin tallentui |
| herätys (WakeToRun) | **ei koskaan** | käyttäjän ehdoton vaatimus; asennus lukee tallennetun tehtävän takaisin ja peruu itsensä, jos herätys olisi päällä |
| akkuvirta | sallittu | oletuksena Windows ohittaisi ajon akkuvirralla hiljaa |
| `-LogonType Interactive` | – | vain kun käyttäjä on kirjautuneena, ikkuna näkyy, salasanaa ei tallenneta |
| `-RunLevel Limited` | – | ei järjestelmänvalvojan oikeuksia |
| `-MultipleInstances Parallel` | – | auki unohtunut ikkuna ei estä seuraavan päivän ajoa; ajolukko estää silti kaksi samanaikaista ajoa |
| `-ExecutionTimeLimit` | 12 h | unohtunut ikkuna suljetaan |

Toiminnon argumentissa on `-ExecutionPolicy Bypass` kuten `.cmd`-
käynnistimissäkin, koska koneen suoritusmääritys on AllSigned.

**Kokeilu heti** (ei odota klo 10:tä — `core/ajastus.py` päättää silti
ajetaanko jotain): Tehtävien ajoitus → Sijoitusanalysaattori → Suorita, tai

```
Start-ScheduledTask -TaskName Sijoitusanalysaattori
```

**Kuiva-ajo** näyttää päätöksen ajamatta mitään:

```
.venv\Scripts\python.exe -m core.ajastus --kuiva
```

Loki on `data\ajoloki.txt`:ssä. **Jos repo siirretään** toiseen kansioon,
asennus ajetaan uudelleen, koska skriptin polku tallentuu tehtävään.
