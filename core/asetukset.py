"""Sijoitusvakiot ja API-avainten haku ympäristömuuttujista."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

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
