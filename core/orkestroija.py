import sys

from agents import paatoksentekija, raportoija, tekninen_analyytikko, trendinmetsastaja
from core import loki, tietokanta

_loki = loki.hae_loki(__name__)

_AGENTIT = [
    ("Agentti 1 - Trendinmetsastaja", trendinmetsastaja),
    ("Agentti 2 - Tekninen analyytikko", tekninen_analyytikko),
    ("Agentti 3 - Paatoksentekija", paatoksentekija),
    ("Agentti 4 - Raportoija", raportoija),
]


def aja_normaali(polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    ajo_id = tietokanta.luo_ajo_id()
    ajotyyppi = "normaali"

    tietokanta.alusta_tietokanta(polku)
    _loki.info(f"Ajo {ajo_id} ({ajotyyppi}) alkaa")

    for nimi, moduuli in _AGENTIT:
        try:
            yhteenveto = moduuli.aja(ajo_id, ajotyyppi, polku=polku)
            _loki.info(f"{nimi}: {yhteenveto}")
        except Exception as virhe:
            _loki.error(f"{nimi} epaonnistui ajossa {ajo_id}: {virhe}")
            raise RuntimeError(
                f"Ajo {ajo_id} keskeytyi: {nimi} epaonnistui - {virhe}"
            ) from virhe

    _loki.info(f"Ajo {ajo_id} ({ajotyyppi}) valmis")
    return ajo_id


if __name__ == "__main__":
    try:
        aja_normaali()
    except RuntimeError as virhe:
        print(f"VIRHE: {virhe}")
        sys.exit(1)
