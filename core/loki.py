import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOKITIEDOSTO = DATA_DIR / "loki.txt"

_JUURINIMI = "sijoitusanalysaattori"
_alustettu = False


def _alusta_lokitus() -> None:
    global _alustettu
    if _alustettu:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    muoto = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    tiedosto_handler = logging.FileHandler(LOKITIEDOSTO, encoding="utf-8")
    tiedosto_handler.setFormatter(muoto)

    ruutu_handler = logging.StreamHandler()
    ruutu_handler.setFormatter(muoto)

    juuri = logging.getLogger(_JUURINIMI)
    juuri.setLevel(logging.INFO)
    juuri.addHandler(tiedosto_handler)
    juuri.addHandler(ruutu_handler)
    juuri.propagate = False

    _alustettu = True


def hae_loki(nimi: str) -> logging.Logger:
    _alusta_lokitus()
    return logging.getLogger(f"{_JUURINIMI}.{nimi}")
