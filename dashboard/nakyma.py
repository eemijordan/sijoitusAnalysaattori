"""Streamlit-nakyma: yksi nappi ajolle, kuusi ruutua edellisen ajon tuloksille.

Nakyma on pelkka lukija - se kayttaa vain core/tietokanta.py:n get_*-funktioita.
Ainoa kirjoittaja on aliprosessina ajettava core.orkestroija (ks. _aja_orkestroija).
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# core-paketti on repon juuressa, dashboard/-alikansiossa itsessaan ei -
# Streamlit lisaa oletuksena vain skriptin oman kansion sys.pathiin, joten
# repon juuri pitaa lisata kasin ennen core-importteja.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import porssiaika, tietokanta  # noqa: E402

st.set_page_config(page_title="Sijoitusanalysaattori", layout="wide")
st.title("Sijoitusanalysaattori")


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
    valmis); jos Popen itse epaonnistuu (esim. venv puuttuu), poikkeus
    nousee kutsujalle asti - sita ei niellä tassa, jotta kutsuja voi
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
    st.subheader("Parhaat pisteet")
    parhaat, virhe = _turvallinen_haku(tietokanta.get_best_scores, days=30, limit=10)
    if virhe:
        st.info(virhe)
    elif not parhaat:
        st.caption("Ei pisteytyksiä viimeisen 30 päivän ajalta.")
    else:
        df = pd.DataFrame(parhaat)[["symbol", "risk_reward_score", "created_at"]]
        df["omistus"] = df["symbol"].apply(
            lambda s: "Kyllä" if s in omistetut_symbolit else "Ei"
        )
        df = df.rename(
            columns={
                "symbol": "Symboli",
                "risk_reward_score": "RR-pisteet",
                "created_at": "Pisteytetty",
                "omistus": "Omistus",
            }
        )
        st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

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
                "target_price": "Tavoite",
                "buy_date": "Ostettu",
            }
        )
        st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

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
                    "target_price": "Tavoite",
                    "rr_ratio": "RR-suhde",
                }
            )
            st.dataframe(_tyhjat_viivoiksi(df), hide_index=True, width="stretch")

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
    elif paluukoodi is not None and paluukoodi != 0:
        tila_paikka.error(
            f"Ajo epäonnistui (paluukoodi {paluukoodi}). Katso loki ylla."
        )

    # Rerun vain onnistuneen ajon jalkeen: se pyyhkisi juuri piirretyn lokin
    # ja tilailmoituksen, jotka epaonnistumisen/kieltaytymisen jalkeen
    # pitaa jaada nakyviin.
    if paluukoodi == 0:
        st.rerun()
