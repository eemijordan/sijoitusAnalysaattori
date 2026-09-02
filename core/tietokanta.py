import sqlite3
from datetime import datetime
from pathlib import Path

from core.asetukset import ALKUPAAOMA

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OLETUS_TIETOKANTA = DATA_DIR / "sijoitusanalysaattori.db"

SKEEMA = """
CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    alkupaaoma REAL NOT NULL,
    kateinen REAL NOT NULL,
    valuutta TEXT NOT NULL DEFAULT 'USD',
    paivitetty TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trend_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ajo_id TEXT NOT NULL,
    ajotyyppi TEXT NOT NULL,
    symboli TEXT NOT NULL,
    lahde TEXT NOT NULL,
    syy TEXT,
    onko_syy_jarkeva INTEGER,
    syy_perustelu TEXT,
    viiteostohinta REAL,
    sijoitus INTEGER,
    jatkoon INTEGER NOT NULL DEFAULT 0,
    pvm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS technical_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ajo_id TEXT NOT NULL,
    ajotyyppi TEXT NOT NULL,
    symboli TEXT NOT NULL,
    risk_reward_pisteet REAL NOT NULL,
    indikaattorit_json TEXT,
    perustelu TEXT,
    pvm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ajo_id TEXT NOT NULL,
    ajotyyppi TEXT NOT NULL,
    symboli TEXT NOT NULL,
    paatos TEXT NOT NULL CHECK (paatos IN ('osta', 'pida', 'myy', 'hylatty')),
    perustelu TEXT,
    pvm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symboli TEXT NOT NULL,
    tila TEXT NOT NULL CHECK (tila IN ('aktiivinen', 'suljettu')),
    ostohinta REAL NOT NULL,
    maara REAL NOT NULL,
    panos_usd REAL NOT NULL,
    stop_loss REAL,
    tavoitehinta REAL,
    ostopvm TEXT NOT NULL,
    osto_ajo_id TEXT NOT NULL,
    myyntihinta REAL,
    myyntipvm TEXT,
    voitto_tappio_usd REAL,
    voitto_tappio_pct REAL,
    viimeisin_tarkistus TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_aktiivinen_symboli
ON positions(symboli) WHERE tila = 'aktiivinen';
"""


def avaa_yhteys(polku=OLETUS_TIETOKANTA):
    Path(polku).parent.mkdir(parents=True, exist_ok=True)
    yhteys = sqlite3.connect(polku)
    yhteys.row_factory = sqlite3.Row
    return yhteys


def alusta_tietokanta(polku=OLETUS_TIETOKANTA):
    yhteys = avaa_yhteys(polku)
    try:
        yhteys.executescript(SKEEMA)

        rivimaara = yhteys.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
        if rivimaara == 0:
            yhteys.execute(
                "INSERT INTO portfolio (id, alkupaaoma, kateinen, valuutta, paivitetty) "
                "VALUES (1, ?, ?, ?, ?)",
                (ALKUPAAOMA, ALKUPAAOMA, "USD", datetime.now().isoformat(timespec="seconds")),
            )
        yhteys.commit()
    finally:
        yhteys.close()


def luo_ajo_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def hae_kateinen(polku=OLETUS_TIETOKANTA) -> float:
    yhteys = avaa_yhteys(polku)
    try:
        rivi = yhteys.execute(
            "SELECT kateinen FROM portfolio WHERE id = 1"
        ).fetchone()
        if rivi is None:
            raise RuntimeError("portfolio-taulu on tyhja - aja alusta_tietokanta() ensin")
        return rivi["kateinen"]
    finally:
        yhteys.close()


def muuta_kateista(delta: float, polku=OLETUS_TIETOKANTA) -> float:
    yhteys = avaa_yhteys(polku)
    try:
        rivi = yhteys.execute(
            "SELECT kateinen FROM portfolio WHERE id = 1"
        ).fetchone()
        if rivi is None:
            raise RuntimeError("portfolio-taulu on tyhja - aja alusta_tietokanta() ensin")

        uusi_saldo = rivi["kateinen"] + delta
        if uusi_saldo < 0:
            raise ValueError(
                f"kateinen menisi negatiiviseksi: {rivi['kateinen']} + {delta} = {uusi_saldo}"
            )

        yhteys.execute(
            "UPDATE portfolio SET kateinen = ?, paivitetty = ? WHERE id = 1",
            (uusi_saldo, datetime.now().isoformat(timespec="seconds")),
        )
        yhteys.commit()
        return uusi_saldo
    finally:
        yhteys.close()


def hae_aktiiviset_positiot(polku=OLETUS_TIETOKANTA) -> list[dict]:
    yhteys = avaa_yhteys(polku)
    try:
        rivit = yhteys.execute(
            "SELECT * FROM positions WHERE tila = 'aktiivinen'"
        ).fetchall()
        return [dict(rivi) for rivi in rivit]
    finally:
        yhteys.close()


def onko_jo_aktiivinen(symboli: str, polku=OLETUS_TIETOKANTA) -> bool:
    yhteys = avaa_yhteys(polku)
    try:
        rivi = yhteys.execute(
            "SELECT 1 FROM positions WHERE symboli = ? AND tila = 'aktiivinen' LIMIT 1",
            (symboli,),
        ).fetchone()
        return rivi is not None
    finally:
        yhteys.close()
