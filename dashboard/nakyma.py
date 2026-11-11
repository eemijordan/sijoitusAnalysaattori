"""Streamlit-nakyma: ajonapit, salkun tuotto, ajon tulokset ja tapahtumat.

Asettelu on neljassa osassa: ylarivi (Hallinta, Parhaat pisteet nyt,
Omistukset), salkun tuotto SPY:hyn verrattuna (vaihe 5d), valitun ajon
tulokset (Agentit 1-3) ja kuukauden osto- ja myyntitapahtumat (vaihe 5e).

Nakyma on pelkka lukija - se kayttaa vain core/tietokanta.py:n get_*-funktioita.
Ainoa kirjoittaja on aliprosessina ajettava core.orkestroija (ks. _aja_orkestroija).
"""

import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

# core-paketti on repon juuressa, dashboard/-alikansiossa itsessaan ei -
# Streamlit lisaa oletuksena vain skriptin oman kansion sys.pathiin, joten
# repon juuri pitaa lisata kasin ennen core-importteja.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import porssiaika, tietokanta  # noqa: E402
from core.asetukset import (  # noqa: E402
    BENCHMARK_SYMBOL,
    MAX_POSITIONS,
    POSITION_SIZE,
    REPURCHASE_COOLDOWN_DAYS,
    RR_THRESHOLD,
)

st.set_page_config(page_title="Sijoitusanalysaattori", layout="wide")
st.title("Sijoitusanalysaattori")

# Pisteytys on jatkuva 0,1 tarkkuudella (vaihe 2k) - ilman muotoilua 7,3
# voisi nakya vaarin.
_RR_PISTEET_SARAKE = {"RR-pisteet": st.column_config.NumberColumn(format="%.1f")}

# Tapahtumaloki muotoilee luvut tekstiksi (vaihe 5e, ks. _tapahtumataulukko),
# joten oikea tasaus annetaan erikseen - tekstisarake tasautuisi vasemmalle.
_TAPAHTUMIEN_LUKUSARAKKEET = {
    sarake: st.column_config.TextColumn(alignment="right")
    for sarake in ("Hinta (USD)", "Määrä", "Summa (USD)", "Tulos (USD)", "Tulos (%)")
}


def _turvallinen_haku(funktio, *args, **kwargs):
    """Ajaa lukufunktion ja nappaa tietokantalukon siistiksi viestiksi.

    Jos ajo on kesken ja nakyma lukee samaan aikaan, SQLite voi palauttaa
    "database is locked". Sivun ei pida silloin kaatua, vaan ruudun pitaa
    kertoa lyhyesti etta tietokanta on varattu.
    """
    try:
        return funktio(*args, **kwargs), None
    except sqlite3.OperationalError as virhe:
        return None, f"Tietokanta on varattu, ajo kesken ({virhe})."


def _tyhjat_viivoiksi(df: pd.DataFrame) -> pd.DataFrame:
    """Korvaa tyhjät arvot ajatusviivalla näyttöä varten.

    pandas näyttää tietokannan NULL-arvot muodossa None tai NaN, mikä on
    Pythonin sisäistä sanastoa eikä kuulu käyttöliittymään. Palauttaa aina
    kopion - alkuperäinen DataFrame ei muutu, jotta sama taulukko ei näytä
    eri asiaa siitä riippuen missä järjestyksessä sitä käsitellään.

    Käsittelee vain sarakkeet joissa on oikeasti tyhjiä arvoja: koskematon
    sarake pysyy numeerisena ja lajittuu oikein (10 > 9), eikä pelkkä nolla,
    tyhjä merkkijono tai False muutu viivaksi - ne ovat oikeita arvoja,
    pd.isna() ei tunnista niitä tyhjiksi. Sarake jossa on jo yksikin tyhjä
    arvo muuttuu joka tapauksessa sekamuotoiseksi heti kun NULL korvataan
    viivalla, joten lajittelun kannalta ei ole mitään menetettävää.
    """
    df = df.copy()
    for sarake in df.columns:
        if df[sarake].isna().any():
            df[sarake] = df[sarake].apply(lambda arvo: "-" if pd.isna(arvo) else arvo)
    return df


def _suomi_pvm(pvm: str) -> str:
    """Muuntaa ISO-päivämäärän (2026-09-07) suomalaiseen muotoon (7.9.2026).

    Ei strftime("%-d"): se ei toimi Windowsissa.
    """
    vuosi, kuukausi, paiva = pvm.split("-")
    return f"{int(paiva)}.{int(kuukausi)}.{vuosi}"


def _muotoile_tuotto(arvo: float | None, yksikko: str = "%") -> str:
    """Muotoilee tuoton etumerkillä ja kahdella desimaalilla, tyhjän viivaksi."""
    if pd.isna(arvo):
        return "-"
    return f"{arvo:+.2f} {yksikko}"


def _tarkistus_vanhentunut(viimeisin_alku: str | None, tanaan: date) -> str | None:
    """Palauttaa varoitustekstin, jos onnistuneesta tarkistuksesta on yli 3 päivää.

    Automaattinen ajo voi epäonnistua kenenkään huomaamatta, jolloin stoppeja
    ei tarkisteta (vaihe 6a). Raja on 3 päivää, koska normaali väli on
    lauantaista tiistaihin.
    """
    if viimeisin_alku is None:
        return (
            "Onnistunutta tarkistusajoa ei ole vielä. Myyntisääntöjä (stop ja EMA) "
            "ei ole tarkistettu."
        )
    paivia = (tanaan - date.fromisoformat(viimeisin_alku[:10])).days
    if paivia <= 3:
        return None
    return (
        f"Edellisestä onnistuneesta tarkistuksesta on {paivia} päivää. "
        "Myyntisääntöjä (stop ja EMA) ei ole tarkistettu sen jälkeen."
    )


def _suomi_aika(aikaleima: str) -> str:
    """Muuntaa ISO-aikaleiman muotoon 3.11.2026 klo 15.39 (vaihe 5e).

    Pelkkä päivämäärä (ilman T-osaa) muotoillaan ilman kellonaikaa.
    """
    if "T" not in aikaleima:
        return _suomi_pvm(aikaleima[:10])
    paiva, kello = aikaleima.split("T", 1)
    tunnit, minuutit = kello.split(":")[:2]
    return f"{_suomi_pvm(paiva)} klo {int(tunnit)}.{minuutit}"


def _kuukauden_nimi(kuukausi: str) -> str:
    """Muuntaa kuukausiavaimen (2026-11) muotoon Marraskuu 2026 (vaihe 5e)."""
    nimet = (
        "Tammikuu",
        "Helmikuu",
        "Maaliskuu",
        "Huhtikuu",
        "Toukokuu",
        "Kesäkuu",
        "Heinäkuu",
        "Elokuu",
        "Syyskuu",
        "Lokakuu",
        "Marraskuu",
        "Joulukuu",
    )
    vuosi, kk = kuukausi.split("-")
    return f"{nimet[int(kk) - 1]} {vuosi}"


def _kuukaudet(tapahtumat: list[dict], tanaan: date) -> list[str]:
    """Palauttaa kuukaudet ensimmäisestä tapahtumasta tähän kuuhun, uusin ensin.

    Kuluva kuukausi on aina mukana, vaikka siinä ei olisi tapahtumia: valinta
    alkaa siitä, ja tyhjä kuukausi näkyy tyhjänä eikä katoa listasta
    (vaihe 5e).
    """
    vuosi, kk = tanaan.year, tanaan.month
    alku = min(
        (tapahtuma["time"][:7] for tapahtuma in tapahtumat),
        default=f"{vuosi:04d}-{kk:02d}",
    )
    kuukaudet = []
    while True:
        avain = f"{vuosi:04d}-{kk:02d}"
        kuukaudet.append(avain)
        if avain <= alku:
            return kuukaudet
        kk -= 1
        if kk == 0:
            vuosi, kk = vuosi - 1, 12


def _tapahtumataulukko(tapahtumat: list[dict], kuukausi: str) -> pd.DataFrame:
    """Rajaa tapahtumat kuukauteen ja muotoilee ne näytettäväksi (vaihe 5e).

    Luvut muotoillaan tekstiksi kiinteillä desimaaleilla, koska ostorivin
    tulos on viiva, eikä viiva ja luku samassa sarakkeessa salli
    numeromuotoilua. Järjestys säilyy sellaisenaan (uusin ensin).
    """
    sarakkeet = [
        "Aika",
        "Symboli",
        "Tapahtuma",
        "Hinta (USD)",
        "Määrä",
        "Summa (USD)",
        "Tulos (USD)",
        "Tulos (%)",
        "Perustelu",
    ]
    rivit = []
    for tapahtuma in tapahtumat:
        if not tapahtuma["time"].startswith(kuukausi):
            continue
        tulos_usd = tapahtuma["pnl_usd"]
        rivit.append(
            {
                "Aika": _suomi_aika(tapahtuma["time"]),
                "Symboli": tapahtuma["symbol"],
                "Tapahtuma": "Myynti" if tapahtuma["kind"] == "myynti" else "Osto",
                "Hinta (USD)": f"{tapahtuma['price']:.2f}",
                "Määrä": f"{tapahtuma['quantity']:.4f}",
                "Summa (USD)": f"{tapahtuma['amount_usd']:.2f}",
                "Tulos (USD)": "-" if tulos_usd is None else f"{tulos_usd:+.2f}",
                "Tulos (%)": _muotoile_tuotto(tapahtuma["pnl_pct"]),
                "Perustelu": tapahtuma["rationale"] or "-",
            }
        )
    return pd.DataFrame(rivit, columns=sarakkeet)


def _kuukauden_yhteenveto(tapahtumat: list[dict], kuukausi: str) -> str:
    """Kokoaa kuukauden ostot, myynnit ja toteutuneen tuloksen riville (vaihe 5e)."""
    kuun = [t for t in tapahtumat if t["time"].startswith(kuukausi)]
    ostoja = sum(1 for t in kuun if t["kind"] == "osto")
    myynnit = [t for t in kuun if t["kind"] == "myynti"]
    tulos = sum(t["pnl_usd"] or 0.0 for t in myynnit)
    return (
        f"{ostoja} {'osto' if ostoja == 1 else 'ostoa'} · "
        f"{len(myynnit)} {'myynti' if len(myynnit) == 1 else 'myyntiä'} · "
        f"toteutunut tulos {tulos:+.2f} USD"
    )


def _kielto_ohitettu(pisteytys: dict, kielto: dict) -> bool:
    """Kertoo, ohittaako nousutrendin paluu takaisinostokiellon (vaihe 5e).

    Sama sääntö kuin Agentti 3:n _check_cooldown_bypass():ssa (vaihe 3e):
    EMA20 on nyt EMA100:n yläpuolella ja viimeisin risteys on myyntipäivänä
    tai sen jälkeen. Näkymä ei tuo agents-pakettia, koska tuonti alustaisi
    lokituksen (loki.get_logger moduulitasolla) dashboard-prosessiin. Testi
    test_kielto_ohitettu_vastaa_agentti3_saantoa pitää säännöt samoina.
    """
    nopea = pisteytys.get("ema_fast")
    hidas = pisteytys.get("ema_slow")
    risteys = pisteytys.get("ema_cross_date")
    if nopea is None or hidas is None or risteys is None:
        return False
    return nopea >= hidas and risteys >= kielto["sold_on"]


def _pisteiden_tila(
    symboli: str,
    pisteet: float,
    kielto: dict | None,
    kielto_ohitettu: bool,
    *,
    omistetut: set[str],
    ostoraja: float,
    salkku_taynna: bool,
    kateinen_riittaa: bool,
) -> str:
    """Kertoo lyhyesti, voiko osakkeen ostaa nyt ja jos ei, miksi (vaihe 5e).

    Tarkistukset ovat samassa järjestyksessä kuin Agentti 3:lla: omistus ja
    kielto, sitten ostoraja, sitten salkun tila. Lopullinen päätös tehdään
    vasta normaaliajossa, jossa tarkistetaan lisäksi mm. viitehintaraja ja
    kahden oston raja ajoa kohden.
    """
    if symboli in omistetut:
        return "Omistuksessa"
    if kielto is not None and not kielto_ohitettu:
        return f"Kielto {_suomi_pvm(kielto['until'])} asti"
    if pisteet < ostoraja:
        return "Alle ostorajan"
    if salkku_taynna:
        return "Salkku täynnä"
    if not kateinen_riittaa:
        return "Käteinen ei riitä"
    if kielto is not None:
        return "Ostokelpoinen, kielto ohitettu"
    return "Ostokelpoinen"


def _tuottosarja(tilannekuvat: list[dict]) -> pd.DataFrame:
    """Muuntaa tilannekuvat tuottosarjaksi: yksi rivi per päivä (vaihe 5d).

    Lähtöpiste on ensimmäinen tilannekuva jolla on vertailukurssi - sama
    sääntö kuin Agentti 4:llä (agents/raportoija.py), jotta viimeinen rivi on
    sama luku kuin tarkistusajon lokirivi. Sitä aiemmat tilannekuvat jäävät
    pois. Samalta päivältä käytetään viimeisintä tilannekuvaa (lista on
    id-järjestyksessä). Tuotot prosentteina, pyöristämättä.
    """
    sarakkeet = ["pvm", "salkku_pct", "vertailu_pct", "ero_pct"]
    lahto_indeksi = next(
        (
            indeksi
            for indeksi, kuva in enumerate(tilannekuvat)
            if kuva["benchmark_price"] is not None
        ),
        None,
    )
    if lahto_indeksi is None:
        return pd.DataFrame(columns=sarakkeet)
    lahto = tilannekuvat[lahto_indeksi]

    paivan_viimeisin: dict[str, dict] = {}
    for kuva in tilannekuvat[lahto_indeksi:]:
        paivan_viimeisin[kuva["snapshot_date"]] = kuva

    rivit = []
    for pvm, kuva in paivan_viimeisin.items():
        salkku = (kuva["total_value"] / lahto["total_value"] - 1) * 100
        vertailu = None
        ero = None
        if kuva["benchmark_price"] is not None:
            vertailu = (kuva["benchmark_price"] / lahto["benchmark_price"] - 1) * 100
            ero = salkku - vertailu
        rivit.append(
            {"pvm": pvm, "salkku_pct": salkku, "vertailu_pct": vertailu, "ero_pct": ero}
        )
    return pd.DataFrame(rivit, columns=sarakkeet)


def _tuottokaavio(sarja: pd.DataFrame, vertailu_nimi: str) -> alt.LayerChart:
    """Piirtää salkun ja vertailuindeksin tuoton viivoina samalle akselille.

    Värit on tarkistettu värisokeus- ja kontrastitarkistimella sekä vaaleaa
    (#ffffff) että tummaa (#0e1117) Streamlit-taustaa vasten, joten samat
    heksat toimivat molemmissa teemoissa. Vain yksi y-akseli.
    """
    salkku_vari = "#2a78d6"
    vertailu_vari = "#d95926"
    apuviivan_vari = "#898781"

    pitka = sarja.melt(
        id_vars=["pvm"],
        value_vars=["salkku_pct", "vertailu_pct"],
        var_name="sarja",
        value_name="tuotto",
    ).dropna(subset=["tuotto"])
    pitka["sarja"] = pitka["sarja"].map(
        {"salkku_pct": "Salkku", "vertailu_pct": vertailu_nimi}
    )
    levea = sarja.assign(
        paiva=sarja["pvm"].map(_suomi_pvm),
        salkku=sarja["salkku_pct"].map(_muotoile_tuotto),
        vertailu=sarja["vertailu_pct"].map(_muotoile_tuotto),
        ero=sarja["ero_pct"].map(lambda arvo: _muotoile_tuotto(arvo, "%-yks")),
    )

    x = alt.X(
        "pvm:T",
        title=None,
        scale=alt.Scale(type="utc"),
        axis=alt.Axis(format="%-d.%-m.", labelAngle=0, grid=False),
    )
    pohja = alt.Chart(pitka).encode(
        x=x,
        y=alt.Y(
            "tuotto:Q", title="Tuotto aloituksesta (%)", axis=alt.Axis(format=".1f")
        ),
        color=alt.Color(
            "sarja:N",
            title=None,
            scale=alt.Scale(
                domain=["Salkku", vertailu_nimi], range=[salkku_vari, vertailu_vari]
            ),
            legend=alt.Legend(orient="top", symbolType="stroke"),
        ),
    )
    viivat = pohja.mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round")
    pisteet = pohja.mark_point(filled=True, size=50, opacity=1)
    nollaviiva = (
        alt.Chart()
        .mark_rule(color=apuviivan_vari, strokeWidth=1)
        .encode(y=alt.datum(0))
    )

    valinta = alt.selection_point(
        nearest=True, on="pointerover", fields=["pvm"], empty=False
    )
    hiusviiva = (
        alt.Chart(levea)
        .mark_rule(color=apuviivan_vari, strokeWidth=1)
        .encode(
            x=x,
            opacity=alt.when(valinta).then(alt.value(1)).otherwise(alt.value(0)),
            tooltip=[
                alt.Tooltip("paiva:N", title="Päivä"),
                alt.Tooltip("salkku:N", title="Salkku"),
                alt.Tooltip("vertailu:N", title=vertailu_nimi),
                alt.Tooltip("ero:N", title="Ero"),
            ],
        )
        .add_params(valinta)
    )
    return alt.layer(nollaviiva, viivat, pisteet, hiusviiva).properties(height=300)


def _ajoikkuna_huomautus(nyt_et: datetime) -> str | None:
    """Palauttaa huomautustekstin jos data ei ole viela valmis, muuten None.

    Kayttaa samaa porssiaika.is_data_ready()-tarkistusta jota orkestroija
    kayttaa ajon kieltamiseen - nakyma ja orkestroija katsovat samaa
    totuutta. Korvaa vanhan "porssi auki" -palkin, joka nayttaytyi vain
    porssin aukioloaikana: ajo kieltaytyy myos klo 16:00-24:00 New Yorkin
    aikaa porssin sulkeuduttua, koska paiva ei vaihdu ennen keskiyota -
    siina ikkunassa vanha palkki nayttai vihreaa valoa ja punaisen virheen
    perään. Kertoo milloin ajo onnistuu, ei mika on kiinni.
    """
    valmis, _ = porssiaika.is_data_ready(nyt_et)
    if valmis:
        return None
    return "Päivän data ei ole vielä valmis. Ajo onnistuu klo 07:00–16:30 Suomen aikaa."


def _aja_orkestroija(ajotyyppi: str, loki_paikka) -> int:
    """Kaynnistaa core.orkestroijan aliprosessina ja nayttaa lokin livena.

    Aliprosessina eika suorana run_normal()-kutsuna, jotta agentin
    kaatuminen ei kaada koko nakymaa. -u pakottaa puskuroimattoman
    tulostuksen (muuten et nakisi mitaan ennen kuin ajo on ohi), ja
    stderr=STDOUT yhdistaa virhevirran koska Pythonin logging kirjoittaa
    oletuksena sinne - ilman yhdistamista live-loki olisi tyhja vaikka ajo
    toimisi. Palauttaa paluukoodin (0 onnistui, 1 kaatui, 2 data ei ollut
    valmis, 3 toinen ajo kaynnissa); jos Popen itse epaonnistuu (esim. venv
    puuttuu), poikkeus nousee kutsujalle asti - sita ei niellä tassa, jotta kutsuja voi
    nayttaa sen kayttajalle eika mikaan epaonnistu hiljaa.

    EI kirjoita lopputulosta loki_paikka:an - vain lokirivit. Kutsuja
    paattaa miten paluukoodi nayttaytyy (vari, teksti), jotta lokiplaceholder
    ei tyhjenny/ylikirjoitu ennen kuin kayttaja nakee sen sisallon.
    """
    venv_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    process = subprocess.Popen(
        [str(venv_python), "-u", "-m", "core.orkestroija", ajotyyppi],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(REPO_ROOT),
    )

    rivit: list[str] = []
    for rivi in process.stdout:
        rivit.append(rivi.rstrip())
        loki_paikka.code("\n".join(rivit))

    return process.wait()


# Vanhentumisvaroitus heti otsikon alla. Tietokantalukon aikana (ajo kesken)
# varoitus jatetaan nayttamatta - ajo on silloin juuri kaynnissa.
viimeisin_tarkistus, virhe_tarkistus = _turvallinen_haku(
    tietokanta.get_last_completed_run, "tarkistus"
)
if not virhe_tarkistus:
    _varoitus = _tarkistus_vanhentunut(
        viimeisin_tarkistus["started_at"] if viimeisin_tarkistus else None,
        date.today(),
    )
    if _varoitus is not None:
        st.warning(_varoitus)

avoimet_positiot, virhe_positiot = _turvallinen_haku(tietokanta.get_open_positions)
omistetut_symbolit = (
    {p["symbol"] for p in avoimet_positiot} if avoimet_positiot else set()
)

ylarivi = st.columns(3)

with ylarivi[0], st.container(border=True):
    st.subheader("Hallinta")
    _huomautus = _ajoikkuna_huomautus(datetime.now(ZoneInfo("America/New_York")))
    if _huomautus is not None:
        st.info(_huomautus)
    # Napit pysyvat silti kaytettavissa - kayttaja paattaa itse, ainoa syy
    # poistaa napit kaytosta on etta ajo on parhaillaan kaynnissa.
    ajo_kaynnissa = st.session_state.get("ajo_kaynnissa", False)
    klik_tarkistus = st.button(
        "Tarkista omistukset",
        disabled=ajo_kaynnissa,
        width="stretch",
        help="Ajaa tarkistusajon: kayr avoimet positiot lapi ja paattaa pito/myynti.",
    )
    klik_normaali = st.button(
        "Tee täysi skannaus markkinoille",
        disabled=ajo_kaynnissa,
        width="stretch",
        help="Ajaa normaaliajon: etsii uusia ehdokkaita ja pisteyttaa/ostaa niita.",
    )
    loki_paikka = st.empty()
    tila_paikka = st.empty()

with ylarivi[1], st.container(border=True):
    st.subheader("Parhaat pisteet nyt")
    parhaat, virhe = _turvallinen_haku(tietokanta.get_latest_scores, days=30, limit=10)
    if virhe:
        st.info(virhe)
    elif not parhaat:
        st.caption("Ei pisteytyksiä viimeisen 30 päivän ajalta.")
    else:
        kiellot, virhe_kiellot = _turvallinen_haku(
            tietokanta.get_cooldown_symbols, REPURCHASE_COOLDOWN_DAYS, date.today()
        )
        kateinen_nyt, virhe_kateinen_nyt = _turvallinen_haku(tietokanta.get_cash)
        df = pd.DataFrame(parhaat)[["symbol", "risk_reward_score", "created_at"]]
        if virhe_kiellot or virhe_kateinen_nyt:
            # Tila jaa viivaksi: arvaus olisi pahempi kuin tyhja sarake.
            df["tila"] = None
        else:
            df["tila"] = [
                _pisteiden_tila(
                    rivi["symbol"],
                    rivi["risk_reward_score"],
                    kiellot.get(rivi["symbol"]),
                    rivi["symbol"] in kiellot
                    and _kielto_ohitettu(rivi, kiellot[rivi["symbol"]]),
                    omistetut=omistetut_symbolit,
                    ostoraja=RR_THRESHOLD,
                    salkku_taynna=len(omistetut_symbolit) >= MAX_POSITIONS,
                    kateinen_riittaa=kateinen_nyt >= POSITION_SIZE,
                )
                for rivi in parhaat
            ]
        df["created_at"] = df["created_at"].apply(_suomi_aika)
        df = df.rename(
            columns={
                "symbol": "Symboli",
                "risk_reward_score": "RR-pisteet",
                "created_at": "Pisteytetty",
                "tila": "Tila",
            }
        )
        st.dataframe(
            _tyhjat_viivoiksi(df),
            hide_index=True,
            width="stretch",
            column_config=_RR_PISTEET_SARAKE,
        )
        positiokoko = f"{POSITION_SIZE:,}".replace(",", " ")
        kateisteksti = (
            ""
            if kateinen_nyt is None
            else " (nyt " + f"{kateinen_nyt:,.2f}".replace(",", " ") + " USD)"
        )
        st.caption(
            "Jokaisen osakkeen viimeisin pisteytys 30 päivän ajalta. Ostoraja on "
            f"{RR_THRESHOLD:g} pistettä, ja osto vaatii {positiokoko} USD "
            f"käteistä{kateisteksti}. Lopullinen päätös tehdään normaaliajossa."
        )

with ylarivi[2], st.container(border=True):
    st.subheader("Omistukset")
    if virhe_positiot:
        st.info(virhe_positiot)
    elif not avoimet_positiot:
        st.caption("Ei avoimia positioita.")
    else:
        df = pd.DataFrame(avoimet_positiot)[
            [
                "symbol",
                "buy_price",
                "quantity",
                "stake_usd",
                "stop_loss",
                "target_price",
                "buy_date",
            ]
        ]
        df = df.rename(
            columns={
                "symbol": "Symboli",
                "buy_price": "Ostohinta",
                "quantity": "Määrä",
                "stake_usd": "Panos (USD)",
                "stop_loss": "Stop",
                "target_price": "52 vk huippu",
                "buy_date": "Ostettu",
            }
        )
        st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

with st.container(border=True):
    st.subheader("Salkun tuotto")
    tuoton_kuvat, virhe_tuotto = _turvallinen_haku(tietokanta.get_snapshots)
    if virhe_tuotto:
        st.info(virhe_tuotto)
    else:
        sarja = _tuottosarja(tuoton_kuvat or [])
        if sarja.empty:
            st.caption(
                "Ei vielä tilannekuvia. Tilannekuva syntyy, kun ajat "
                "Tarkista omistukset."
            )
        else:
            viimeisin = sarja.iloc[-1]
            luvut = st.columns(3)
            luvut[0].metric(
                "Salkku",
                _muotoile_tuotto(viimeisin["salkku_pct"]),
                help="Koko salkun (käteinen + osakkeet) tuotto lähtöpisteestä.",
            )
            luvut[1].metric(
                BENCHMARK_SYMBOL,
                _muotoile_tuotto(viimeisin["vertailu_pct"]),
                help="Vertailuindeksin tuotto samalta ajalta.",
            )
            luvut[2].metric(
                f"Ero {BENCHMARK_SYMBOL}:hyn",
                _muotoile_tuotto(viimeisin["ero_pct"], "%-yks"),
                help="Salkun tuotto miinus vertailuindeksin tuotto.",
            )
            st.caption(
                f"Lähtöpiste {_suomi_pvm(sarja.iloc[0]['pvm'])} · viimeisin "
                f"tilannekuva {_suomi_pvm(viimeisin['pvm'])} · {len(sarja)} "
                "mittauspäivää. Tilannekuva syntyy vain Tarkista omistukset -ajossa."
            )
            if len(sarja) >= 2:
                st.altair_chart(_tuottokaavio(sarja, BENCHMARK_SYMBOL), width="stretch")
            else:
                st.caption(
                    "Graafi piirtyy, kun tilannekuvia on vähintään kahdelta päivältä."
                )

st.divider()
st.subheader("Valitun ajon tulokset")

ajot, virhe_ajot = _turvallinen_haku(tietokanta.get_recent_runs, limit=20)

if virhe_ajot:
    st.info(virhe_ajot)
elif not ajot:
    st.caption("Ei ajoja vielä.")
else:
    valittu_ajo = st.selectbox(
        "Valitse ajo",
        options=ajot,
        format_func=lambda ajo: (
            f"{ajo['run_id']} | {ajo['run_type']} | {ajo['started_at']} | "
            f"{ajo['status']}"
        ),
    )
    run_id = valittu_ajo["run_id"]

    alarivi = st.columns([1, 1, 1.4])

    with alarivi[0], st.container(border=True):
        # Tarkistusajossa Agentti 1 ei pisteyta eika jarjesta ehdokkaita -
        # se listaa omistetut positiot Agentti 2:lle hinnoiteltavaksi, ei
        # etsi uusia ostettavia. score/rank ovat silloin NULL taysin
        # oikeutetusti, ja "advanced" olisi aina Kylla (kaikki omistetut
        # positiot menevat maaritelmallisesti jatkoon) - kolme saraketta
        # joista ei saisi mitaan tietoa. run_type on tiedossa etukateen
        # alasvetovalikosta, joten ratkaisu perustuu siihen eika siihen
        # sattuvatko sarakkeet olemaan tyhjia talla kertaa.
        tarkistusajo = valittu_ajo["run_type"] == "tarkistus"
        st.markdown(
            "**Agentti 1 – tarkistettavat positiot**"
            if tarkistusajo
            else "**Agentti 1 – ehdokkaat**"
        )
        ehdokkaat, virhe = _turvallinen_haku(
            tietokanta.get_candidates, run_id, advanced_only=False
        )
        if virhe:
            st.info(virhe)
        elif not ehdokkaat:
            st.caption(
                "Ei tarkistettavia positioita tällä ajolla."
                if tarkistusajo
                else "Ei ehdokkaita tällä ajolla."
            )
        else:
            if tarkistusajo:
                df = pd.DataFrame(ehdokkaat)[["symbol", "source", "reason"]]
                df = df.rename(
                    columns={
                        "symbol": "Symboli",
                        "source": "Lähde",
                        "reason": "Peruste",
                    }
                )
            else:
                df = pd.DataFrame(ehdokkaat)[
                    ["symbol", "source", "score", "rank", "advanced"]
                ]
                df["advanced"] = df["advanced"].apply(lambda a: "Kyllä" if a else "Ei")
                df = df.rename(
                    columns={
                        "symbol": "Symboli",
                        "source": "Lähde",
                        "score": "Pisteet",
                        "rank": "Sijoitus",
                        "advanced": "Jatkoon",
                    }
                )
            st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

    with alarivi[1], st.container(border=True):
        st.markdown("**Agentti 2 – pisteytykset**")
        pisteytykset, virhe = _turvallinen_haku(tietokanta.get_scores, run_id)
        if virhe:
            st.info(virhe)
        elif not pisteytykset:
            st.caption("Ei pisteytyksiä tällä ajolla.")
        else:
            df = pd.DataFrame(pisteytykset)[
                [
                    "symbol",
                    "risk_reward_score",
                    "price",
                    "stop_loss",
                    "stop_source",
                    "target_price",
                    "rr_ratio",
                ]
            ]
            df = df.rename(
                columns={
                    "symbol": "Symboli",
                    "risk_reward_score": "RR-pisteet",
                    "price": "Hinta",
                    "stop_loss": "Stop",
                    "stop_source": "Stop-peruste",
                    "target_price": "52 vk huippu",
                    "rr_ratio": "RR-suhde",
                }
            )
            st.dataframe(
                _tyhjat_viivoiksi(df),
                hide_index=True,
                width="stretch",
                column_config=_RR_PISTEET_SARAKE,
            )

        # Tyhja osio jatetaan kokonaan pois - tyhja otsikko olisi kohinaa
        # (ks. CLAUDE.md, vaihe 2i). Nakyy vain kun tama ajo oikeasti ohitti
        # symboleja, joten CVLT-tyyppinen katoaminen ei jaa enaa arvailun
        # varaan.
        ohitukset, virhe_ohitukset = _turvallinen_haku(tietokanta.get_skips, run_id)
        if virhe_ohitukset:
            st.info(virhe_ohitukset)
        elif ohitukset:
            st.markdown("**Ohitetut**")
            df = pd.DataFrame(ohitukset)[["symbol", "stage", "reason"]]
            df = df.rename(
                columns={
                    "symbol": "Symboli",
                    "stage": "Vaihe",
                    "reason": "Syy",
                }
            )
            st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

    with alarivi[2], st.container(border=True):
        st.markdown("**Agentti 3 – päätökset ja salkun tilanne**")
        paatokset, virhe = _turvallinen_haku(tietokanta.get_decisions, run_id)
        if virhe:
            st.info(virhe)
        elif not paatokset:
            st.caption("Ei päätöksiä tällä ajolla.")
        else:
            df = pd.DataFrame(paatokset)[["symbol", "decision", "rationale"]]
            df = df.rename(
                columns={
                    "symbol": "Symboli",
                    "decision": "Päätös",
                    "rationale": "Perustelu",
                }
            )
            st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

        st.divider()
        kateinen, virhe_kateinen = _turvallinen_haku(tietokanta.get_cash)
        if virhe_kateinen:
            st.info(virhe_kateinen)
        else:
            st.metric("Käteinen", f"{kateinen:,.2f} USD".replace(",", " "))

        tilannekuvat, virhe_tilannekuvat = _turvallinen_haku(tietokanta.get_snapshots)
        if not virhe_tilannekuvat and tilannekuvat:
            viimeisin = tilannekuvat[-1]
            st.caption(
                f"Viimeisin tilannekuva ({viimeisin['snapshot_date']}): "
                f"salkku {viimeisin['total_value']:,.2f} USD".replace(",", " ")
            )

st.divider()
st.subheader("Tapahtumat")

tapahtumat, virhe_tapahtumat = _turvallinen_haku(tietokanta.get_transactions)
if virhe_tapahtumat:
    st.info(virhe_tapahtumat)
else:
    tapahtumat = tapahtumat or []
    valittu_kuukausi = st.columns([1, 3])[0].selectbox(
        "Kuukausi",
        options=_kuukaudet(tapahtumat, date.today()),
        format_func=_kuukauden_nimi,
        key="tapahtumat_kuukausi",
    )
    taulukko = _tapahtumataulukko(tapahtumat, valittu_kuukausi)
    if taulukko.empty:
        st.caption("Ei osto- tai myyntitapahtumia tässä kuussa.")
    else:
        st.caption(_kuukauden_yhteenveto(tapahtumat, valittu_kuukausi))
        st.dataframe(
            taulukko,
            hide_index=True,
            width="stretch",
            column_config=_TAPAHTUMIEN_LUKUSARAKKEET,
        )

if klik_tarkistus or klik_normaali:
    ajotyyppi = "tarkistus" if klik_tarkistus else "normaali"
    st.session_state["ajo_kaynnissa"] = True
    paluukoodi = None
    try:
        paluukoodi = _aja_orkestroija(ajotyyppi, loki_paikka)
    except Exception as virhe:
        # Ei saa epaonnistua hiljaa - esim. Popen voi heittaa jos venv
        # puuttuu. Virhe nakyy lokipaikassa, ei kaadu koko sivua.
        loki_paikka.exception(virhe)
    finally:
        # Aina, tapahtui mita tahansa - muuten poikkeus jattaisi napit
        # pysyvasti pois kaytosta eika kayttaja paasisi yrittamaan uudelleen.
        st.session_state["ajo_kaynnissa"] = False

    # Paluukoodi 2 (core/orkestroija.py: DataNotReadyError) ei ole
    # kaatuminen vaan kieltaytyminen - eri vari, eri teksti, ei runs-riviä
    # syntynyt. tila_paikka on oma paikkansa loki_paikan ALLA samassa
    # Hallinta-ruudussa, joten loki_paikan sisalto ei katoa kun tila
    # kirjoitetaan - "katso loki ylla" pitaa taas paikkansa.
    if paluukoodi == 2:
        tila_paikka.info(
            "Markkinadata ei ollut vielä valmis - mitään ei kirjattu. "
            "Yritä uudestaan aamulla."
        )
    elif paluukoodi == 3:
        # core/ajolukko.py: RunLockedError - myos kieltaytyminen, ei kaatuminen
        # (vaihe 6a).
        tila_paikka.info(
            "Toinen ajo on jo käynnissä (esim. ajastettu ajo) - mitään ei "
            "kirjattu. Odota, että se valmistuu, ja yritä sitten uudelleen."
        )
    elif paluukoodi is not None and paluukoodi != 0:
        tila_paikka.error(
            f"Ajo epäonnistui (paluukoodi {paluukoodi}). Katso loki ylla."
        )

    # Rerun vain onnistuneen ajon jalkeen: se pyyhkisi juuri piirretyn lokin
    # ja tilailmoituksen, jotka epaonnistumisen/kieltaytymisen jalkeen
    # pitaa jaada nakyviin.
    if paluukoodi == 0:
        st.rerun()
