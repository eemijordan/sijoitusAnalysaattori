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
VALITTAVAT = 8            # montako ehdokasta etenee Agentti 2:lle


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
