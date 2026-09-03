import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "loki.txt"

_ROOT_NAME = "sijoitusanalysaattori"
_initialized = False


def _init_logging() -> None:
    global _initialized
    if _initialized:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.propagate = False

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    _init_logging()
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
