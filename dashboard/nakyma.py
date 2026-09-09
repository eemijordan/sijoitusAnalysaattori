"""Streamlit-nakyma: yksi nappi ajolle, kuusi ruutua edellisen ajon tuloksille.

Nakyma on pelkka lukija - se kayttaa vain core/tietokanta.py:n get_*-funktioita.
Ainoa kirjoittaja on aliprosessina ajettava core.orkestroija (ks. _aja_orkestroija).
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from datetime import time as kellonaika
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

from core import tietokanta  # noqa: E402

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


def _porssi_on_auki(nyt_ny: datetime) -> bool:
    """Karkea arvio: US-porssi auki ma-pe klo 9:30-16:00 New Yorkin aikaa.

    Ei huomioi pyhapaivia - riittava tarkkuus kayttajalle nayttavaan
    huomautukseen, ei paatoksentekoon (vrt. tekninen_analyytikko.py:n
    is_bar_ready(), joka on tarkka koska sen varaan nojaa ajon keskeytys).
    """
    if nyt_ny.weekday() >= 5:  # 5 = lauantai, 6 = sunnuntai
        return False
    return kellonaika(9, 30) <= nyt_ny.time() < kellonaika(16, 0)


def _aja_orkestroija(ajotyyppi: str, loki_paikka) -> int:
    """Kaynnistaa core.orkestroijan aliprosessina ja nayttaa lokin livena.

    Aliprosessina eika suorana run_normal()-kutsuna, jotta agentin
    kaatuminen ei kaada koko nakymaa. -u pakottaa puskuroimattoman
    tulostuksen (muuten et nakisi mitaan ennen kuin ajo on ohi), ja
    stderr=STDOUT yhdistaa virhevirran koska Pythonin logging kirjoittaa
    oletuksena sinne - ilman yhdistamista live-loki olisi tyhja vaikka ajo
    toimisi. Palauttaa paluukoodin; jos Popen itse epaonnistuu (esim. venv
    puuttuu), poikkeus nousee kutsujalle asti - sita ei niellä tassa, jotta
    kutsuja voi nayttaa sen kayttajalle eika mikaan epaonnistu hiljaa.
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

    paluukoodi = process.wait()
    if paluukoodi == 0:
        loki_paikka.success(f"Ajo valmis (paluukoodi {paluukoodi}).")
    else:
        loki_paikka.error(
            f"Ajo epaonnistui (paluukoodi {paluukoodi}). Katso loki ylla."
        )
    return paluukoodi


avoimet_positiot, virhe_positiot = _turvallinen_haku(tietokanta.get_open_positions)
omistetut_symbolit = (
    {p["symbol"] for p in avoimet_positiot} if avoimet_positiot else set()
)

ylarivi = st.columns(3)

with ylarivi[0], st.container(border=True):
    st.subheader("Hallinta")
    if _porssi_on_auki(datetime.now(ZoneInfo("America/New_York"))):
        st.info(
            "US-pörssi on nyt auki. Ajo todennäköisesti keskeytyy, koska "
            "päivän palkki on kesken."
        )
    ajo_kaynnissa = st.session_state.get("ajo_kaynnissa", False)
    klik_tarkistus = st.button(
        "Tarkista omistukset",
        disabled=ajo_kaynnissa,
        use_container_width=True,
        help="Ajaa tarkistusajon: kayr avoimet positiot lapi ja paattaa pito/myynti.",
    )
    klik_normaali = st.button(
        "Tee täysi skannaus markkinoille",
        disabled=ajo_kaynnissa,
        use_container_width=True,
        help="Ajaa normaaliajon: etsii uusia ehdokkaita ja pisteyttaa/ostaa niita.",
    )
    loki_paikka = st.empty()

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
        st.dataframe(df, hide_index=True, use_container_width=True)

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
        st.dataframe(df, hide_index=True, use_container_width=True)

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
        st.markdown("**Agentti 1 – ehdokkaat**")
        ehdokkaat, virhe = _turvallinen_haku(
            tietokanta.get_candidates, run_id, advanced_only=False
        )
        if virhe:
            st.info(virhe)
        elif not ehdokkaat:
            st.caption("Ei ehdokkaita tällä ajolla.")
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
            st.dataframe(df, hide_index=True, use_container_width=True)

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
                    "target_price": "Tavoite",
                    "rr_ratio": "RR-suhde",
                }
            )
            st.dataframe(df, hide_index=True, use_container_width=True)

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
            st.dataframe(df, hide_index=True, use_container_width=True)

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

    # Rerun vain onnistuneen ajon jalkeen: se pyyhkisi juuri piirretyn lokin
    # ja virheilmoituksen, jotka epaonnistumisen jalkeen pitaa jaada nakyviin.
    if paluukoodi == 0:
        st.rerun()
