"""Yhteinen lokitus tiedostoon ja konsoliin."""

import logging
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "loki.txt"

_ROOT_NAME = "sijoitusanalysaattori"
_initialized = False

# Molemmat API:t saavat avaimen URL:n kyselyosassa, ja requestsin
# virheviestit sisaltavat URL:n (vaihe 6b, ks. CLAUDE.md).
_APIKEY_PATTERN = re.compile(r"(apikey=)[A-Za-z0-9_\-]+", re.IGNORECASE)
_KEY_ENV_NAMES = ("FMP_API_KEY", "TWELVE_DATA_API_KEY")


def mask_secrets(text: str | None) -> str | None:
    """Peittää API-avaimet tekstistä ennen lokia, kantaa tai ruutua.

    Kaksi kerrosta: apikey=...-kyselyparametri (requests-virheiden URL:t) ja
    ympäristömuuttujien avainarvot sellaisinaan (esim. params-sanakirjan repr).
    """
    if text is None:
        return None
    text = _APIKEY_PATTERN.sub(r"\1***", text)
    for name in _KEY_ENV_NAMES:
        key = os.environ.get(name)
        if key and len(key) >= 8:
            text = text.replace(key, "***")
    return text


class _SecretMaskFilter(logging.Filter):
    """Peittää API-avaimet jokaisesta lokirivistä ennen kirjoitusta."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage())
        record.args = None
        return True


def _init_logging() -> None:
    global _initialized
    if _initialized:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(_SecretMaskFilter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(_SecretMaskFilter())

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.propagate = False

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Palauttaa nimetyn loggerin, alustaen tiedosto- ja konsolikäsittelijät ensin."""
    _init_logging()
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
