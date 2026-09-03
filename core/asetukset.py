import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Sijoitusvakiot (ks. CLAUDE.md)
ALKUPAAOMA = 10000       # USD
POSITION_KOKO = 1000     # USD per positio (10 %)
MAX_POSITIOT = 10
RR_KYNNYS = 7             # minimipisteet ostolle
EHDOKAS_TAVOITE = 20      # montako ehdokasta Agentti 1 hakee ennen karsintaa
VALITTAVAT = 8            # jatkoon menevien YLARAJA (ei tavoite)
# Analyytikkorivi "piti ennallaan + Buy" saa 3 pistetta, aito korotus + Buy
# saa 5, ja merkittava tuore osto saa 4-5. Kynnys 4 karsii siis juuri ne
# rivit joilla ei tapahtunut mitaan, mutta paastaa lapi aidot signaalit.
EHDOKAS_KYNNYS = 4        # minimipisteet jotta ehdokas etenee Agentti 2:lle
MIN_OSAKEKURSSI = 5.0     # USD; alle taman jaavat ovat liian pieni ja
                          # epalikvidi kohde 1000 USD:n positiolle
# 3 pistetta on alin taso jolla lahde on sanonut jotain positiivista (esim.
# luokitus Buy, tavoite 15-60 % ylapuolella, tai merkittava tuore osto).
# Sita heikompi rivi on lasna mutta ei ole signaali.
YHTEISESIINTYMA_KYNNYS = 3


def _hae_api_avain(ymparistomuuttuja: str) -> str:
    arvo = os.getenv(ymparistomuuttuja)
    if not arvo:
        raise RuntimeError(
            f"API-avain '{ymparistomuuttuja}' puuttuu tai on tyhja. "
            f"Lisaa se .env-tiedostoon projektin juureen, esim: "
            f"{ymparistomuuttuja}=oma_avain"
        )
    return arvo


def hae_twelve_data_avain() -> str:
    return _hae_api_avain("TWELVE_DATA_API_KEY")


def hae_fmp_avain() -> str:
    return _hae_api_avain("FMP_API_KEY")
