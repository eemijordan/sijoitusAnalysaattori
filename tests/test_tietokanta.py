import pytest

from core import tietokanta
from core.asetukset import ALKUPAAOMA

ODOTETUT_TAULUT = {
    "portfolio",
    "trend_candidates",
    "technical_scores",
    "decisions",
    "positions",
}


@pytest.fixture
def db_polku(tmp_path):
    return tmp_path / "testi.db"


def _taulut(polku):
    yhteys = tietokanta.avaa_yhteys(polku)
    try:
        rivit = yhteys.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {rivi["name"] for rivi in rivit}
    finally:
        yhteys.close()


def test_taulut_syntyvat(db_polku):
    tietokanta.alusta_tietokanta(db_polku)
    assert ODOTETUT_TAULUT.issubset(_taulut(db_polku))


def test_portfolio_alustuu_alkupaaomaan(db_polku):
    tietokanta.alusta_tietokanta(db_polku)
    assert tietokanta.hae_kateinen(db_polku) == ALKUPAAOMA


def test_alusta_tietokanta_on_turvallista_ajaa_kahdesti(db_polku):
    tietokanta.alusta_tietokanta(db_polku)
    tietokanta.alusta_tietokanta(db_polku)

    yhteys = tietokanta.avaa_yhteys(db_polku)
    try:
        maara = yhteys.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
    finally:
        yhteys.close()

    assert maara == 1
    assert tietokanta.hae_kateinen(db_polku) == ALKUPAAOMA


def test_onko_jo_aktiivinen(db_polku):
    tietokanta.alusta_tietokanta(db_polku)
    assert tietokanta.onko_jo_aktiivinen("AAPL", db_polku) is False

    yhteys = tietokanta.avaa_yhteys(db_polku)
    try:
        yhteys.execute(
            """
            INSERT INTO positions (
                symboli, tila, ostohinta, maara, panos_usd, ostopvm, osto_ajo_id
            ) VALUES ('AAPL', 'aktiivinen', 100, 10, 1000, '2026-01-01', 'ajo1')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    assert tietokanta.onko_jo_aktiivinen("AAPL", db_polku) is True
    assert tietokanta.onko_jo_aktiivinen("MSFT", db_polku) is False


def test_muuta_kateista_kasvattaa_ja_vahentaa(db_polku):
    tietokanta.alusta_tietokanta(db_polku)

    uusi_saldo = tietokanta.muuta_kateista(-1000, db_polku)
    assert uusi_saldo == ALKUPAAOMA - 1000
    assert tietokanta.hae_kateinen(db_polku) == ALKUPAAOMA - 1000

    uusi_saldo = tietokanta.muuta_kateista(500, db_polku)
    assert uusi_saldo == ALKUPAAOMA - 500
    assert tietokanta.hae_kateinen(db_polku) == ALKUPAAOMA - 500


def test_muuta_kateista_estaa_negatiivisen_saldon(db_polku):
    tietokanta.alusta_tietokanta(db_polku)

    with pytest.raises(ValueError):
        tietokanta.muuta_kateista(-(ALKUPAAOMA + 1), db_polku)

    assert tietokanta.hae_kateinen(db_polku) == ALKUPAAOMA
