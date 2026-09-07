"""Sijoitusvakiot ja API-avainten haku ympäristömuuttujista."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Nostetaan aina kun pistemalli tai siihen vaikuttava vakio muuttuu (ks.
# CLAUDE.md) - ihmisluettava merkintä. Varsinainen totuus ajon toistamiseksi
# on git-commit (ks. core/orkestroija.py), koska se ei voi unohtua.
MODEL_VERSION = "1.1"

# Sijoitusvakiot (ks. CLAUDE.md)
INITIAL_CAPITAL = 10000  # USD
POSITION_SIZE = 1000  # USD per positio (10 %)
MAX_POSITIONS = 10
RR_THRESHOLD = 7  # minimipisteet ostolle
CANDIDATE_TARGET = 20  # montako ehdokasta Agentti 1 hakee ennen karsintaa
MAX_SELECTED = 8  # jatkoon menevien YLÄRAJA (ei tavoite)
# Analyytikkorivi "piti ennallaan + Buy" saa 3 pistettä, aito korotus + Buy
# saa 5, ja merkittävä tuore osto saa 4-5. Kynnys 4 karsii siis juuri ne
# rivit joilla ei tapahtunut mitään, mutta päästää läpi aidot signaalit.
CANDIDATE_THRESHOLD = 4  # minimipisteet jotta ehdokas etenee Agentti 2:lle
MIN_SHARE_PRICE = 5.0  # USD; alle tämän jäävät ovat liian pieni ja
# epälikvidi kohde 1000 USD:n positiolle
# 3 pistettä on alin taso jolla lähde on sanonut jotain positiivista (esim.
# luokitus Buy, tavoite 15-60 % yläpuolella, tai merkittävä tuore osto).
# Sitä heikompi rivi on läsnä mutta ei ole signaali.
CO_OCCURRENCE_THRESHOLD = 3
# Markkina-arvo ei ole saatavilla ilmaistasolla (FMP palauttaa 402:n pienille
# yhtiöille, Twelve Datan Basicissa ei ole fundamenttidataa), joten
# keskimääräinen päivävaihto USD:ssa toimii yrityskoon korvikkeena.
MIN_DAILY_TURNOVER = 5_000_000  # USD/päivä, keskiarvo 252 päivältä. Karsii
# mikroyhtiöt; keskikokoiset yhtiöt ylittävät
# tämän selvästi.
# Agentti 1:n signaali voi olla jopa 14 vrk vanha. Jos hinta on ehtinyt
# liikkua kauas signaalin hetken hinnasta, tilanne ei ole enää se jota
# signaali kuvasi. Raja on kaksisuuntainen: ylöspäin karannut hinta
# tarkoittaa että sisäänmeno meni ohi, jyrkkä pudotus että jotain tapahtui
# signaalin jälkeen. Molemmat hylätään, mutta eri perustelutekstillä.
MAX_PRICE_DRIFT = 0.05  # 5 %
# Salkkutason pysäytin. Jokainen ostoehto katsoo yhtä osaketta kerrallaan,
# joten ilman tätä botti ostaisi yhtä innokkaasti 20 % pakkasella kuin
# ensimmäisenä päivänä.
PORTFOLIO_STOP_LEVEL = 0.80  # osuus INITIAL_CAPITAL:sta
# Stop asetetaan kahden keskimääräisen päiväliikkeen (ATR) päähän ostohinnasta:
# jos kurssi putoaa enemmän kuin kaksi normaalia päivää, tilanne ei ole enää se
# jonka perusteella ostettiin. Kahta pienempi kerroin laukaisisi stopin
# tavallisen päivittäisheilunnan takia, suurempi tekisi tappioista tarpeettoman
# isoja.
STOP_ATR_MULTIPLIER = 2.0
# Hystereesi, ei sattumanvarainen luku: ostetaan kun pisteet >= RR_THRESHOLD
# (7), myydään vasta kun ne putoavat alle EXIT_SCORE_THRESHOLD:in (5). Väliin
# jäävä kaista 5-7 on tarkoituksellinen - jos osto- ja myyntiraja olisivat
# sama luku, seitsemän pisteen tuntumassa värähtelevä osake ostettaisiin ja
# myytäisiin vuorotellen joka tarkistuksessa. Kaista poistaa sen kokonaan.
EXIT_SCORE_THRESHOLD = 5
# Hajauttaa sisaanmenot ajassa. Ilman rajaa salkku tayttyy noin neljassa
# ajossa eli kahdessa viikossa, jolloin koko salkku edustaa yhta
# markkinatilannetta eika tuloksesta voi paatella mallista mitaan.
MAX_BUYS_PER_RUN = 2
# Stop laukesi koska saanto sanoi etta veto oli vaara. Saman vedon
# asettaminen uudelleen muutaman paivan paasta maksaa saman tappion
# toistamiseen. 30 vrk on riittavan pitka katkaisemaan putoavan puukon ja
# riittavan lyhyt salliakseen aidon paluun myohemmin.
REPURCHASE_COOLDOWN_DAYS = 30
# Twelve Data ei tarjoa indekseja itsessaan, joten S&P 500:n korvikkeena
# kaytetaan sita seuraavaa ETF:aa. Vertailukohdan pitaa olla se mihin raha
# olisi vaihtoehtoisesti laitettu - laaja markkina, ei toinen yksittainen
# osake.
BENCHMARK_SYMBOL = "SPY"


def _get_api_key(env_var: str) -> str:
    value = os.getenv(env_var)
    if not value:
        raise RuntimeError(
            f"API-avain '{env_var}' puuttuu tai on tyhjä. "
            f"Lisää se .env-tiedostoon projektin juureen, esim: "
            f"{env_var}=oma_avain"
        )
    return value


def get_twelve_data_key() -> str:
    """Palauttaa Twelve Data -API-avaimen ympäristömuuttujasta."""
    return _get_api_key("TWELVE_DATA_API_KEY")


def get_fmp_key() -> str:
    """Palauttaa FMP-API-avaimen ympäristömuuttujasta."""
    return _get_api_key("FMP_API_KEY")


def snapshot_settings() -> dict:
    """Palauttaa kaikki moduulin vakiot sanakirjana ajon kirjaamista varten.

    Heijastuksella koko moduulin nimiavaruuden yli, ei käsin ylläpidettävästä
    listasta - muuten lista jäisi jälkeen heti kun seuraava vakio lisätään.
    Mukaan otetaan nimet jotka ovat kokonaan isoilla kirjaimilla ja joiden
    arvo on int, float, str tai bool; funktiot, moduulit ja alaviivalla
    alkavat nimet jäävät automaattisesti pois tyyppitarkistuksen kautta.
    """
    module = sys.modules[__name__]
    return {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("_")
        and name.isupper()
        and isinstance(value, (int, float, str, bool))
    }
