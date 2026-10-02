# sijoitusAnalysaattori

Sääntöpohjainen osakeanalyysijärjestelmä, joka hakee dataa kahdesta ulkoisesta
rajapinnasta, pisteyttää löytämänsä osakkeet ja ylläpitää **fiktiivistä 10 000
dollarin salkkua**.

> Oikeaa rahaa ei liiku missään vaiheessa. Projekti on rakennettu mittaamaan
> toimiiko pisteytysmalli, ei käymään kauppaa. Tämä ei ole sijoitusneuvontaa.

Salkun tuottoa verrataan SPY:hyn eli S&P 500 -indeksiä seuraavaan ETF:ään. Vertailu on
koko projektin ydin: nousumarkkinassa lähes mikä tahansa osakesalkku näyttää voittoa,
joten absoluuttinen tuotto ei kerro mallista mitään. Vain ero vertailuindeksiin kertoo.

## Miten se toimii

Neljä agenttia ajetaan kiinteässä järjestyksessä. **Agentit eivät kutsu toisiaan** —
jokainen lukee edellisen tuloksen tietokannasta ja kirjoittaa omansa sinne. SQLite on
ainoa jaettu tila, mikä tekee jokaisesta vaiheesta erikseen tarkasteltavan ja
testattavan.

| Agentti | Tehtävä |
|---|---|
| **1 · trendinmetsästäjä** | Hakee ehdokkaita Financial Modeling Prepistä: sisäpiirikaupat, analyytikkoarviot, hintatavoitteet ja kongressin jäsenten kaupat. Pisteyttää ja karsii. |
| **2 · tekninen analyytikko** | Hakee kurssihistorian Twelve Datasta, laskee tunnusluvut (ATR, RSI, liukuvat keskiarvot) ja antaa risk/reward-pisteet 0–10 sekä stop-loss- ja tavoitehinnan. |
| **3 · päätöksentekijä** | Tekee osto-, pito- ja myyntipäätökset kahdeksan ostoehdon ja viiden pito/myyntiehdon perusteella. Jokainen päätös tallennetaan perusteluineen. |
| **4 · raportoija** | Laskee salkun tilannekuvan ja tuoton sekä kokonaisuutena että pelkkien positioiden osalta, ja vertaa sitä SPY:hyn. |

Ajotyyppejä on kaksi: **normaaliajo** etsii uusia ehdokkaita ja voi avata positioita,
**tarkistusajo** käy avoimet positiot läpi ja päättää pidetäänkö vai myydäänkö.

Jokainen ajo kirjaa `runs`-tauluun käytetyt vakiot, mallin version ja git-commitin.
Kuukausien päästä voi siis selvittää millä säännöillä mikäkin päätös tehtiin.

## Asennus

Vaatii Python 3.10 tai uudemman.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

API-avaimet `.env`-tiedostoon repon juureen (ks. `.env.example`):

```
FMP_API_KEY=oma_avaimesi
TWELVE_DATA_API_KEY=oma_avaimesi
```

Molemmista palveluista saa ilmaisen avaimen. Twelve Datan ilmaistaso sallii 800 kutsua
vuorokaudessa, mihin järjestelmä on mitoitettu.

## Käyttö

Selainkäyttöliittymä, josta ajot käynnistetään ja tulokset näkyvät:

```bash
scripts\nakyma.cmd              # tai: .\scripts\nakyma.ps1
```

Avautuu osoitteeseen `localhost:8501`. Palvelin elää vain niin kauan kuin ikkuna on
auki — koneelle ei jää mitään taustalle pyörimään.

Pelkkä ajo ilman käyttöliittymää:

```bash
scripts\aja.cmd                             # tarkistusajo
.\scripts\aja.ps1 -Ajotyyppi normaali       # uusien ehdokkaiden haku
.\scripts\aja.ps1 -Ajotyyppi molemmat       # tarkistus ensin, sitten normaali
```

### Ajastus

```bash
scripts\ajastus_asenna.cmd      # asentaa ajastetun ajon Windowsin Tehtävien ajoitukseen
scripts\ajastus_poista.cmd      # poistaa sen; ajot tehdään taas vain käsin
```

Ajastettu ajo tekee tarkistuksen ti–la ja normaaliajon ti ja pe klo 10 Suomen aikaa,
aina tarkistuksen ensin. Jos kone on silloin kiinni, ajo tehdään kun kone avataan, mutta
vain jos New Yorkin pörssi ei ole vielä auennut. Väliin jäänyt ajo ei siirry seuraavalle
päivälle. Kone ei koskaan herää ajoa varten, ajo tehdään vain kirjautuneena, ja ikkuna
näkyy aina. Ajastettu ajo ei aja committaamatonta `agents/`- tai `core/`-koodia.
Tarkemmin: `scripts/README.md`.

### Milloin ajo onnistuu

**Klo 07:00–16:30 Suomen aikaa, arkisin.**

Analyysi perustuu päättyneisiin pörssipäiviin. Kesken olevan päivän kynttilän huippu,
pohja ja päätöskurssi muuttuvat vielä, mikä vääristäisi esimerkiksi 52 viikon huipun.
Järjestelmä kieltäytyy ajamasta sen ulkopuolella eikä kirjaa kieltäytymisestä mitään —
ei tietokantariviä eikä API-kutsuja.

Huomaa että ikkuna sulkeutuu pörssin **avautuessa**, ei sulkeutuessa: klo 16:30 Suomen
aikaa on 09:30 New Yorkissa. Illalla klo 23 data on yhä kesken, koska New Yorkissa on
sama päivä keskiyöhön asti.

## Rakenne

```
agents/       neljä agenttia, yksi tiedosto kukin
core/         orkestroija, tietokanta, asetukset, pörssiaika, lokitus
dashboard/    Streamlit-käyttöliittymä (lukee kantaa, ei kirjoita siihen)
scripts/      käynnistimet (.ps1 ja .cmd)
tests/        yksikkötestit
tools/        api_koetin.py — rajapintojen käsin kokeiluun
data/         tietokanta ja raakadata-arkisto (ei versionhallinnassa)
```

Päätöslogiikka on erotettu puhtaiksi funktioiksi tiedon hausta, joten pisteytys ja
päätökset ovat testattavissa ilman verkkoa ja tietokantaa.

## Testit

```bash
pytest                      # yli 250 testiä
ruff check .
ruff format --check .
```

Jokainen kehitysvaihe päättyi siihen että testit ja Ruff menivät läpi ennen commitia.

## Keskeiset säännöt

Kaikki mallin vakiot ovat yhdessä tiedostossa (`core/asetukset.py`) ja tallentuvat
jokaiseen ajoon:

| | |
|---|---|
| Aloituspääoma | 10 000 USD |
| Positiokoko | 1 000 USD (10 % salkusta) |
| Positioita enintään | 10 |
| Ostoja per ajo enintään | 2 |
| Ostoraja | risk/reward-pisteet ≥ 7 |
| Stop-loss | 2 × ATR ostohinnasta |
| Takaisinosto samaan symboliin | aikaisintaan 30 päivän kuluttua |
| Vertailuindeksi | SPY |

## Tunnetut rajoitukset

- **Stop toteutuu vasta seuraavan aamun tarkistuksessa.** Ajot tulevat ajastettuna
  ti–la klo 10, kun kone on käytössä. Stop tarkistetaan edellisen päivän
  päätöskurssia vastaan, ja päivinä jolloin konetta ei avata, tarkistusta ei tehdä
  lainkaan — kone ei herää itsestään. Epäsäännölliset välit näkyvät `runs`-taulun
  aikaleimoista.
- **Toteutunutta historiaa on vähän.** Suljettuja kauppoja ei vielä ole tarpeeksi,
  jotta mallin toimivuudesta voisi sanoa mitään.
- **Kaupankäyntikuluja ja liukumaa ei mallinneta.** Ostot ja myynnit tapahtuvat
  päätöskurssiin.
- **Pyhäpäiviä ei tunneta kalenterista.** Kellonaikaan perustuva ennakkotarkistus
  päästää pyhäpäivän läpi, jolloin varsinainen datatarkistus keskeyttää ajon.

## Kehityksestä

Koodi on kirjoitettu Claude Codella. Oma osuuteni oli arkkitehtuuri, vaiheiden
määrittely ja tulosten tarkistus: esimerkiksi kesken olevan pörssipäivän aiheuttama
datavirhe, käteispainon salkun tuottolukua vääristävä vaikutus ja jäljettömiin
katoavat ehdokkaat löytyivät ajojen tuloksia analysoimalla, ja johtivat kukin omaan
korjausvaiheeseensa.
