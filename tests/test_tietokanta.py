import sqlite3

import pytest

from core import tietokanta
from core.asetukset import INITIAL_CAPITAL

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
    yhteys = tietokanta.open_connection(polku)
    try:
        rivit = yhteys.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {rivi["name"] for rivi in rivit}
    finally:
        yhteys.close()


def test_taulut_syntyvat(db_polku):
    tietokanta.init_database(db_polku)
    assert ODOTETUT_TAULUT.issubset(_taulut(db_polku))


def test_portfolio_alustuu_alkupaaomaan(db_polku):
    tietokanta.init_database(db_polku)
    assert tietokanta.get_cash(db_polku) == INITIAL_CAPITAL


def test_alusta_tietokanta_on_turvallista_ajaa_kahdesti(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.init_database(db_polku)

    yhteys = tietokanta.open_connection(db_polku)
    try:
        maara = yhteys.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
    finally:
        yhteys.close()

    assert maara == 1
    assert tietokanta.get_cash(db_polku) == INITIAL_CAPITAL


def test_onko_jo_aktiivinen(db_polku):
    tietokanta.init_database(db_polku)
    assert tietokanta.has_open_position("AAPL", db_polku) is False

    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, buy_date, buy_run_id
            ) VALUES ('AAPL', 'aktiivinen', 100, 10, 1000, '2026-01-01', 'ajo1')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    assert tietokanta.has_open_position("AAPL", db_polku) is True
    assert tietokanta.has_open_position("MSFT", db_polku) is False


def test_muuta_kateista_kasvattaa_ja_vahentaa(db_polku):
    tietokanta.init_database(db_polku)

    uusi_saldo = tietokanta.adjust_cash(-1000, db_polku)
    assert uusi_saldo == INITIAL_CAPITAL - 1000
    assert tietokanta.get_cash(db_polku) == INITIAL_CAPITAL - 1000

    uusi_saldo = tietokanta.adjust_cash(500, db_polku)
    assert uusi_saldo == INITIAL_CAPITAL - 500
    assert tietokanta.get_cash(db_polku) == INITIAL_CAPITAL - 500


def test_muuta_kateista_estaa_negatiivisen_saldon(db_polku):
    tietokanta.init_database(db_polku)

    with pytest.raises(ValueError):
        tietokanta.adjust_cash(-(INITIAL_CAPITAL + 1), db_polku)

    assert tietokanta.get_cash(db_polku) == INITIAL_CAPITAL


def _lisaa_positio(yhteys, symboli, tila, osto_ajo_id="ajo1"):
    yhteys.execute(
        """
        INSERT INTO positions (
            symbol, status, buy_price, quantity, stake_usd, buy_date, buy_run_id
        ) VALUES (?, ?, 100, 10, 1000, '2026-01-01', ?)
        """,
        (symboli, tila, osto_ajo_id),
    )


def test_kahta_aktiivista_positiota_samalle_symbolille_ei_voi_lisata(db_polku):
    tietokanta.init_database(db_polku)

    yhteys = tietokanta.open_connection(db_polku)
    try:
        _lisaa_positio(yhteys, "AAPL", "aktiivinen")
        yhteys.commit()

        with pytest.raises(sqlite3.IntegrityError):
            _lisaa_positio(yhteys, "AAPL", "aktiivinen")
            yhteys.commit()
    finally:
        yhteys.close()


def test_symboli_voidaan_ostaa_uudelleen_edellisen_position_sulkeuduttua(db_polku):
    tietokanta.init_database(db_polku)

    yhteys = tietokanta.open_connection(db_polku)
    try:
        _lisaa_positio(yhteys, "AAPL", "suljettu")
        yhteys.commit()

        _lisaa_positio(yhteys, "AAPL", "aktiivinen")
        yhteys.commit()
    finally:
        yhteys.close()

    assert tietokanta.has_open_position("AAPL", db_polku) is True


def test_ehdokkaan_pisteet_tallentuvat_ja_luetaan_takaisin(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        source="analyytikko",
        reason="Testisyy",
        score=7.5,
        rank=1,
        advanced=1,
        db_path=db_polku,
    )

    rivit = tietokanta.get_candidates("ajo1", advanced_only=False, db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["score"] == 7.5
