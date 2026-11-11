import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from core import tietokanta
from core.asetukset import INITIAL_CAPITAL, RR_THRESHOLD

ODOTETUT_TAULUT = {
    "portfolio",
    "trend_candidates",
    "technical_scores",
    "decisions",
    "positions",
    "runs",
    "market_reference",
    "portfolio_snapshots",
    "skipped_symbols",
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


# --- technical_scores: stop_loss, target_price, rr_ratio (vaihe 2g) ---


def test_pisteytys_tallentaa_stopin_tavoitteen_ja_rr_suhteen(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8,
        price=100.0,
        stop_loss=90.0,
        target_price=130.0,
        rr_ratio=3.0,
        db_path=db_polku,
    )

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["stop_loss"] == 90.0
    assert rivit[0]["target_price"] == 130.0
    assert rivit[0]["rr_ratio"] == 3.0


def test_pisteytys_ilman_stop_parametreja_tallentaa_nonet(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8,
        price=100.0,
        db_path=db_polku,
    )

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["stop_loss"] is None
    assert rivit[0]["stop_source"] is None
    assert rivit[0]["target_price"] is None
    assert rivit[0]["rr_ratio"] is None


# --- technical_scores.stop_source (vaihe 2j) ---


def test_pisteytys_tallentaa_stop_sourcen(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8,
        price=100.0,
        stop_loss=90.0,
        stop_source="viikkopohja",
        db_path=db_polku,
    )

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert rivit[0]["stop_source"] == "viikkopohja"


def test_pisteytys_tuntematon_stop_source_nostaa_virheen(db_polku):
    tietokanta.init_database(db_polku)

    with pytest.raises(ValueError):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol="AAPL",
            risk_reward_score=8,
            price=100.0,
            stop_source="jokin_tuntematon",
            db_path=db_polku,
        )


def test_init_database_lisaa_stop_sourcen_vanhaan_kantaan_rivit_sailyvat(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE technical_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                run_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                risk_reward_score REAL NOT NULL,
                price REAL,
                indicators_json TEXT,
                rationale TEXT,
                stop_loss REAL,
                target_price REAL,
                rr_ratio REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.execute(
            """
            INSERT INTO technical_scores (
                run_id, run_type, symbol, risk_reward_score, stop_loss, created_at
            ) VALUES ('ajo1', 'normaali', 'AAPL', 8, 85.0, '2026-09-01T00:00:00')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    lisatyt = tietokanta.init_database(db_polku)

    assert "technical_scores.stop_source" in lisatyt

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["stop_loss"] == 85.0  # vanha rivi sailyy koskemattomana
    assert rivit[0]["stop_source"] is None  # vanhalle riville ei keksita perustetta


# --- technical_scores.ema_fast/ema_slow/ema_cross_date (vaihe 3d) ---


def test_pisteytys_tallentaa_ema_kentat(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8,
        price=100.0,
        ema_fast=95.0,
        ema_slow=90.0,
        ema_cross_date="2026-06-01",
        db_path=db_polku,
    )

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert rivit[0]["ema_fast"] == 95.0
    assert rivit[0]["ema_slow"] == 90.0
    assert rivit[0]["ema_cross_date"] == "2026-06-01"


def test_pisteytys_ilman_ema_parametreja_tallentaa_nonet(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8,
        price=100.0,
        db_path=db_polku,
    )

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert rivit[0]["ema_fast"] is None
    assert rivit[0]["ema_slow"] is None
    assert rivit[0]["ema_cross_date"] is None


def test_init_database_lisaa_ema_sarakkeet_vanhaan_kantaan_rivit_sailyvat(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE technical_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                run_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                risk_reward_score REAL NOT NULL,
                price REAL,
                indicators_json TEXT,
                rationale TEXT,
                stop_loss REAL,
                stop_source TEXT,
                target_price REAL,
                rr_ratio REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.execute(
            """
            INSERT INTO technical_scores (
                run_id, run_type, symbol, risk_reward_score, price, created_at
            ) VALUES ('ajo1', 'normaali', 'AAPL', 8, 100.0, '2026-09-01T00:00:00')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    lisatyt = tietokanta.init_database(db_polku)

    assert set(lisatyt) == {
        "technical_scores.ema_fast",
        "technical_scores.ema_slow",
        "technical_scores.ema_cross_date",
    }

    rivit = tietokanta.get_scores("ajo1", db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["price"] == 100.0  # vanha rivi sailyy koskemattomana
    assert rivit[0]["ema_fast"] is None
    assert rivit[0]["ema_slow"] is None
    assert rivit[0]["ema_cross_date"] is None


# --- get_cooldown_symbols(): vaihe 3c ---


def test_jaahy_voimassa_kun_myyty_hiljattain(db_polku):
    tietokanta.init_database(db_polku)
    tanaan = date(2026, 9, 7)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=db_polku,
    )
    tietokanta.close_position(
        symbol="AAPL", sell_price=90.0, sell_run_id="ajo1", db_path=db_polku
    )
    yhteys = tietokanta.open_connection(db_polku)
    try:
        myyty = (tanaan - timedelta(days=10)).isoformat()
        yhteys.execute(
            "UPDATE positions SET sell_date = ? WHERE symbol = 'AAPL'", (myyty,)
        )
        yhteys.commit()
    finally:
        yhteys.close()

    jaahyt = tietokanta.get_cooldown_symbols(30, tanaan, db_path=db_polku)

    # Vaihe 3e: seka paattymis- etta myyntipaiva palautetaan suoraan.
    assert jaahyt == {
        "AAPL": {
            "until": (tanaan - timedelta(days=10) + timedelta(days=30)).isoformat(),
            "sold_on": (tanaan - timedelta(days=10)).isoformat(),
        }
    }


def test_jaahy_ei_voimassa_kun_myyty_kauan_sitten(db_polku):
    tietokanta.init_database(db_polku)
    tanaan = date(2026, 9, 7)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=db_polku,
    )
    tietokanta.close_position(
        symbol="AAPL", sell_price=90.0, sell_run_id="ajo1", db_path=db_polku
    )
    yhteys = tietokanta.open_connection(db_polku)
    try:
        myyty = (tanaan - timedelta(days=40)).isoformat()
        yhteys.execute(
            "UPDATE positions SET sell_date = ? WHERE symbol = 'AAPL'", (myyty,)
        )
        yhteys.commit()
    finally:
        yhteys.close()

    jaahyt = tietokanta.get_cooldown_symbols(30, tanaan, db_path=db_polku)

    assert "AAPL" not in jaahyt


def test_jaahy_kaytetaan_viimeisinta_myyntipaivaa(db_polku):
    tietokanta.init_database(db_polku)
    tanaan = date(2026, 9, 7)

    yhteys = tietokanta.open_connection(db_polku)
    try:
        # kaksi suljettua positiota samalle symbolille, eri myyntipaivilla
        yhteys.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, buy_date,
                buy_run_id, sell_date
            ) VALUES ('AAPL', 'suljettu', 100, 10, 1000, '2026-01-01', 'ajo1', ?)
            """,
            ((tanaan - timedelta(days=50)).isoformat(),),
        )
        yhteys.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, buy_date,
                buy_run_id, sell_date
            ) VALUES ('AAPL', 'suljettu', 90, 10, 900, '2026-02-01', 'ajo2', ?)
            """,
            ((tanaan - timedelta(days=5)).isoformat(),),
        )
        yhteys.commit()
    finally:
        yhteys.close()

    jaahyt = tietokanta.get_cooldown_symbols(30, tanaan, db_path=db_polku)

    # vanhempi myynti (50 vrk sitten) olisi jo jaahyn ulkopuolella, mutta
    # tuoreempi (5 vrk sitten) on yha voimassa - viimeisin ratkaisee.
    assert jaahyt == {
        "AAPL": {
            "until": (tanaan - timedelta(days=5) + timedelta(days=30)).isoformat(),
            "sold_on": (tanaan - timedelta(days=5)).isoformat(),
        }
    }


def test_jaahy_myyntipaiva_aikaleimasta_paivamaaraksi(db_polku):
    # close_position() kirjoittaa sell_date:n aikaleimana; sold_on on
    # pelkka paivamaara, jotta se on vertailukelpoinen ema_cross_date:n kanssa.
    tietokanta.init_database(db_polku)
    tanaan = date(2026, 9, 17)
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, buy_date,
                buy_run_id, sell_date
            ) VALUES ('NVDA', 'suljettu', 100, 10, 1000, '2026-09-01', 'ajo1',
                      '2026-09-15T08:21:37')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    jaahyt = tietokanta.get_cooldown_symbols(30, tanaan, db_path=db_polku)

    assert jaahyt == {"NVDA": {"until": "2026-10-15", "sold_on": "2026-09-15"}}


def test_jaahy_ohittaa_avoimen_position(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=db_polku,
    )

    jaahyt = tietokanta.get_cooldown_symbols(30, date.today(), db_path=db_polku)

    assert jaahyt == {}


def test_jaahy_tyhja_taulu_palauttaa_tyhjan_sanakirjan(db_polku):
    tietokanta.init_database(db_polku)

    jaahyt = tietokanta.get_cooldown_symbols(30, date.today(), db_path=db_polku)

    assert jaahyt == {}


# --- runs: ajojen versiointi ---


def _kaynnista_ajo(db_polku, run_id="ajo1"):
    tietokanta.start_run(
        run_id=run_id,
        run_type="normaali",
        settings_json=json.dumps({"RR_THRESHOLD": RR_THRESHOLD}),
        model_version="1.0",
        git_commit="deadbeef",
        db_path=db_polku,
    )


def test_kaynnista_ajo_luo_rivin_tilassa_kesken(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku)

    rivi = tietokanta.get_run("ajo1", db_path=db_polku)
    assert rivi["status"] == "kesken"
    assert "RR_THRESHOLD" in json.loads(rivi["settings_json"])


def test_kaynnista_ajo_kirjoittaa_arkistometatiedoston(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku)

    metatiedosto = tietokanta.ARKISTO_DIR / "ajo1" / "_ajo.json"
    assert metatiedosto.exists()
    sisalto = json.loads(metatiedosto.read_text(encoding="utf-8"))
    assert sisalto["run_id"] == "ajo1"
    assert sisalto["status"] == "kesken"


def test_paata_ajo_paivittaa_arkistometatiedoston_statuksen(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku)

    tietokanta.finish_run("ajo1", "valmis", db_path=db_polku)

    metatiedosto = tietokanta.ARKISTO_DIR / "ajo1" / "_ajo.json"
    sisalto = json.loads(metatiedosto.read_text(encoding="utf-8"))
    assert sisalto["status"] == "valmis"
    assert sisalto["finished_at"] is not None


def test_paata_ajo_valmiiksi_asettaa_finished_at_ja_tilan(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku)

    tietokanta.finish_run("ajo1", "valmis", db_path=db_polku)

    rivi = tietokanta.get_run("ajo1", db_path=db_polku)
    assert rivi["status"] == "valmis"
    assert rivi["finished_at"] is not None


def test_paata_ajo_keskeytyi_tallentaa_virhetekstin(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku)

    tietokanta.finish_run(
        "ajo1", "keskeytyi", error="Twelve Data ei vastannut", db_path=db_polku
    )

    rivi = tietokanta.get_run("ajo1", db_path=db_polku)
    assert rivi["status"] == "keskeytyi"
    assert rivi["error"] == "Twelve Data ei vastannut"


def test_kelvoton_status_hylataan_check_rajoitteella(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku)

    with pytest.raises(sqlite3.IntegrityError):
        tietokanta.finish_run("ajo1", "olematon", db_path=db_polku)


# --- mark_stale_runs_interrupted(): vaihe 6 ---


def test_vanha_kesken_ajo_merkitaan_keskeytyneeksi(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku, run_id="ajo1")

    maara = tietokanta.mark_stale_runs_interrupted(db_path=db_polku)

    rivi = tietokanta.get_run("ajo1", db_path=db_polku)
    assert maara == 1
    assert rivi["status"] == "keskeytyi"
    assert rivi["error"] == "ajo ei paattynyt (prosessi katkesi)"
    assert rivi["finished_at"] is not None


def test_useita_vanhoja_kesken_ajoja_merkitaan_kaikki(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku, run_id="ajo1")
    _kaynnista_ajo(db_polku, run_id="ajo2")
    _kaynnista_ajo(db_polku, run_id="ajo3")

    maara = tietokanta.mark_stale_runs_interrupted(db_path=db_polku)

    assert maara == 3
    for run_id in ("ajo1", "ajo2", "ajo3"):
        assert tietokanta.get_run(run_id, db_path=db_polku)["status"] == "keskeytyi"


def test_valmis_ja_keskeytyi_ajoja_ei_kosketa(db_polku):
    tietokanta.init_database(db_polku)
    _kaynnista_ajo(db_polku, run_id="valmis_ajo")
    tietokanta.finish_run("valmis_ajo", "valmis", db_path=db_polku)
    _kaynnista_ajo(db_polku, run_id="keskeytynyt_ajo")
    tietokanta.finish_run(
        "keskeytynyt_ajo", "keskeytyi", error="alkuperainen virhe", db_path=db_polku
    )

    maara = tietokanta.mark_stale_runs_interrupted(db_path=db_polku)

    assert maara == 0
    assert tietokanta.get_run("valmis_ajo", db_path=db_polku)["status"] == "valmis"
    keskeytynyt = tietokanta.get_run("keskeytynyt_ajo", db_path=db_polku)
    assert keskeytynyt["status"] == "keskeytyi"
    assert keskeytynyt["error"] == "alkuperainen virhe"


def test_ei_vanhoja_ajoja_palauttaa_nollan(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.mark_stale_runs_interrupted(db_path=db_polku) == 0


# --- market_reference ja portfolio_snapshots: vaihe 4a ---


def test_vertailukurssi_tallentuu_ja_luetaan_takaisin(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_market_reference(
        run_id="ajo1", symbol="SPY", price=500.0, db_path=db_polku
    )

    rivi = tietokanta.get_market_reference("ajo1", "SPY", db_path=db_polku)
    assert rivi["run_id"] == "ajo1"
    assert rivi["symbol"] == "SPY"
    assert rivi["price"] == 500.0


def test_vertailukurssi_puuttuva_palauttaa_nonen(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_market_reference("ajo1", "SPY", db_path=db_polku) is None


def test_tilannekuva_tallentuu_ja_luetaan_takaisin_aikajarjestyksessa(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-09-01",
        cash=8000.0,
        positions_value=2000.0,
        total_value=10000.0,
        benchmark_symbol="SPY",
        benchmark_price=500.0,
        db_path=db_polku,
    )
    tietokanta.insert_snapshot(
        run_id="ajo2",
        snapshot_date="2026-09-08",
        cash=8000.0,
        positions_value=2200.0,
        total_value=10200.0,
        benchmark_symbol="SPY",
        benchmark_price=505.0,
        db_path=db_polku,
    )

    rivit = tietokanta.get_snapshots(db_path=db_polku)
    assert len(rivit) == 2
    assert rivit[0]["run_id"] == "ajo1"
    assert rivit[1]["run_id"] == "ajo2"
    assert rivit[0]["total_value"] == 10000.0
    assert rivit[1]["benchmark_price"] == 505.0


def test_tilannekuva_ilman_vertailukurssia_tallentaa_nonen(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-09-01",
        cash=10000.0,
        positions_value=0.0,
        total_value=10000.0,
        db_path=db_polku,
    )

    rivit = tietokanta.get_snapshots(db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["benchmark_symbol"] is None
    assert rivit[0]["benchmark_price"] is None


# --- holdings_return_pct: vaihe 4a jatko ---


def test_holdings_return_pct_tallentuu_ja_luetaan_takaisin(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-09-01",
        cash=8000.0,
        positions_value=2000.0,
        total_value=10000.0,
        holdings_return_pct=-1.6,
        db_path=db_polku,
    )

    rivit = tietokanta.get_snapshots(db_path=db_polku)
    assert rivit[0]["holdings_return_pct"] == -1.6


def test_holdings_return_pct_none_tallentuu_ja_luetaan_takaisin(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-09-01",
        cash=10000.0,
        positions_value=0.0,
        total_value=10000.0,
        db_path=db_polku,
    )

    rivit = tietokanta.get_snapshots(db_path=db_polku)
    assert rivit[0]["holdings_return_pct"] is None


def test_init_database_lisaa_holdings_return_pct_vanhaan_kantaan(db_polku):
    # Jaljittelee tuotantokantaa joka luotiin ennen vaihe 4a jatkoa: taulu on
    # jo olemassa ilman uutta saraketta. CREATE TABLE IF NOT EXISTS ei
    # koskaan lisaisi saraketta talleiseen tauluun - _migrate_missing_
    # columns():n (vaihe D2) pitaa tehda se ALTER TABLE:lla.
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                benchmark_symbol TEXT,
                benchmark_price REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    tietokanta.init_database(db_polku)

    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-09-01",
        cash=10000.0,
        positions_value=0.0,
        total_value=10000.0,
        holdings_return_pct=1.5,
        db_path=db_polku,
    )

    rivit = tietokanta.get_snapshots(db_path=db_polku)
    assert rivit[0]["holdings_return_pct"] == 1.5


# --- _schema_columns() / _migrate_missing_columns(): vaihe D2 ---


def test_schema_columns_palauttaa_kaikki_taulut_ja_sarakemaarat():
    sarakkeet = tietokanta._schema_columns()

    assert set(sarakkeet.keys()) == ODOTETUT_TAULUT
    assert len(sarakkeet["portfolio"]) == 5
    assert len(sarakkeet["portfolio_snapshots"]) == 10


def test_migraatio_lisaa_puuttuvan_sarakkeen_ja_palauttaa_sen_nimen(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                benchmark_symbol TEXT,
                benchmark_price REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    lisatyt = tietokanta.init_database(db_polku)

    assert lisatyt == ["portfolio_snapshots.holdings_return_pct"]

    yhteys = tietokanta.open_connection(db_polku)
    try:
        sarakkeet = {
            r["name"] for r in yhteys.execute("PRAGMA table_info(portfolio_snapshots)")
        }
    finally:
        yhteys.close()
    assert "holdings_return_pct" in sarakkeet


def test_migraatio_sailyttaa_datan_uusi_sarake_nullina(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                benchmark_symbol TEXT,
                benchmark_price REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.execute(
            """
            INSERT INTO portfolio_snapshots (
                run_id, snapshot_date, cash, positions_value, total_value,
                created_at
            ) VALUES ('ajo1', '2026-09-01', 10000, 0, 10000, '2026-09-01T00:00:00')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    tietokanta.init_database(db_polku)

    rivit = tietokanta.get_snapshots(db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["run_id"] == "ajo1"
    assert rivit[0]["total_value"] == 10000
    assert rivit[0]["holdings_return_pct"] is None


def test_migraatio_useita_puuttuvia_sarakkeita_eri_tauluissa(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                benchmark_symbol TEXT,
                benchmark_price REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.execute(
            """
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('aktiivinen', 'suljettu')),
                buy_price REAL NOT NULL,
                quantity REAL NOT NULL,
                stake_usd REAL NOT NULL,
                stop_loss REAL,
                target_price REAL,
                buy_date TEXT NOT NULL,
                buy_run_id TEXT NOT NULL,
                sell_price REAL,
                sell_date TEXT,
                sell_run_id TEXT,
                pnl_usd REAL,
                pnl_pct REAL
            )
            """
        )  # last_checked puuttuu
        yhteys.commit()
    finally:
        yhteys.close()

    lisatyt = tietokanta.init_database(db_polku)

    assert set(lisatyt) == {
        "portfolio_snapshots.holdings_return_pct",
        "positions.last_checked",
    }


def test_migraatio_ajantasainen_kanta_ei_lisaa_mitaan(db_polku):
    tietokanta.init_database(db_polku)  # ensimmainen ajo luo taydellisen skeeman

    lisatyt = tietokanta.init_database(db_polku)  # toinen ajo, ei mitaan puuttuvaa

    assert lisatyt == []


def test_migraatio_on_toistettavissa(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                benchmark_symbol TEXT,
                benchmark_price REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    ensimmainen = tietokanta.init_database(db_polku)
    toinen = tietokanta.init_database(db_polku)

    assert ensimmainen == ["portfolio_snapshots.holdings_return_pct"]
    assert toinen == []


def test_migraatio_not_null_ilman_oletusarvoa_kaataa_aanekkaasti(monkeypatch, db_polku):
    # Kayttaa omaa testiskeemaa - oikeaa SCHEMA:a ei muuteta.
    testiskeema = """
    CREATE TABLE IF NOT EXISTS testitaulu (
        id INTEGER PRIMARY KEY,
        pakollinen TEXT NOT NULL
    );
    """
    monkeypatch.setattr(tietokanta, "SCHEMA", testiskeema)

    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute("CREATE TABLE testitaulu (id INTEGER PRIMARY KEY)")
        yhteys.commit()

        with pytest.raises(RuntimeError) as virhe:
            tietokanta._migrate_missing_columns(yhteys)
    finally:
        yhteys.close()

    assert "testitaulu.pakollinen" in str(virhe.value)
    assert "NOT NULL ilman oletusarvoa" in str(virhe.value)


def test_migraatio_ei_poista_ylimaaraista_saraketta(db_polku):
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                benchmark_symbol TEXT,
                benchmark_price REAL,
                holdings_return_pct REAL,
                created_at TEXT NOT NULL,
                kokeilu TEXT
            )
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    tietokanta.init_database(db_polku)

    yhteys = tietokanta.open_connection(db_polku)
    try:
        sarakkeet = {
            r["name"] for r in yhteys.execute("PRAGMA table_info(portfolio_snapshots)")
        }
    finally:
        yhteys.close()
    assert "kokeilu" in sarakkeet


def test_viimeisin_pisteytys_symbolille_palauttaa_uusimman_yli_ajojen(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=100.0,
        db_path=db_polku,
    )
    tietokanta.insert_score(
        run_id="ajo2",
        run_type="tarkistus",
        symbol="AAPL",
        risk_reward_score=6.0,
        price=110.0,
        db_path=db_polku,
    )

    rivi = tietokanta.get_latest_score_for_symbol("AAPL", db_path=db_polku)
    assert rivi["run_id"] == "ajo2"
    assert rivi["price"] == 110.0


def test_viimeisin_pisteytys_puuttuva_symboli_palauttaa_nonen(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_latest_score_for_symbol("AAPL", db_path=db_polku) is None


# --- get_recent_runs() ja get_latest_scores(): vaiheet 5 ja 5e (dashboard) ---


def _kaynnista_ja_paata_ajo(db_polku, run_id):
    tietokanta.start_run(
        run_id=run_id,
        run_type="normaali",
        settings_json="{}",
        model_version="1.0",
        db_path=db_polku,
    )
    tietokanta.finish_run(run_id, "valmis", db_path=db_polku)


def test_viimeisimmat_ajot_uusin_ensin(db_polku):
    tietokanta.init_database(db_polku)
    for run_id in ("ajo1", "ajo2", "ajo3"):
        _kaynnista_ja_paata_ajo(db_polku, run_id)

    ajot = tietokanta.get_recent_runs(db_path=db_polku)

    assert [ajo["run_id"] for ajo in ajot] == ["ajo3", "ajo2", "ajo1"]


def test_viimeisimmat_ajot_rajaa_maaraan(db_polku):
    tietokanta.init_database(db_polku)
    for run_id in ("ajo1", "ajo2", "ajo3"):
        _kaynnista_ja_paata_ajo(db_polku, run_id)

    ajot = tietokanta.get_recent_runs(limit=2, db_path=db_polku)

    assert [ajo["run_id"] for ajo in ajot] == ["ajo3", "ajo2"]


def test_viimeisimmat_ajot_tyhja_kanta_palauttaa_tyhjan_listan(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_recent_runs(db_path=db_polku) == []


def _lisaa_pisteytys(db_polku, symboli, pisteet, paivia_sitten):
    """Lisää pisteytyksen kiinteällä aikaleimalla (insert_score käyttää kelloa)."""
    aika = datetime.now() - timedelta(days=paivia_sitten)
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            """
            INSERT INTO technical_scores (
                run_id, run_type, symbol, risk_reward_score, price, created_at
            ) VALUES ('ajo', 'normaali', ?, ?, 100.0, ?)
            """,
            (symboli, pisteet, aika.isoformat(timespec="seconds")),
        )
        yhteys.commit()
    finally:
        yhteys.close()


def test_viimeisimmat_pisteet_uusin_rivi_voittaa_vaikka_vanha_olisi_parempi(db_polku):
    tietokanta.init_database(db_polku)
    _lisaa_pisteytys(db_polku, "AAPL", 9.0, paivia_sitten=5)
    _lisaa_pisteytys(db_polku, "AAPL", 5.0, paivia_sitten=1)

    pisteet = tietokanta.get_latest_scores(db_path=db_polku)

    assert [(p["symbol"], p["risk_reward_score"]) for p in pisteet] == [("AAPL", 5.0)]


def test_viimeisimmat_pisteet_vanha_huippu_ei_nosta_symbolia(db_polku):
    # 11.11.2026: NVDA:n 13.10. huippu 9,1 näkyi taulukon kärjessä, vaikka
    # osake oli myyty ja nykyiset pisteet olivat 2,4.
    tietokanta.init_database(db_polku)
    _lisaa_pisteytys(db_polku, "NVDA", 9.1, paivia_sitten=29)
    _lisaa_pisteytys(db_polku, "NVDA", 2.4, paivia_sitten=1)
    _lisaa_pisteytys(db_polku, "KO", 7.8, paivia_sitten=1)

    pisteet = tietokanta.get_latest_scores(db_path=db_polku)

    assert [(p["symbol"], p["risk_reward_score"]) for p in pisteet] == [
        ("KO", 7.8),
        ("NVDA", 2.4),
    ]


def test_viimeisimmat_pisteet_jattaa_pois_ikkunaa_vanhemmat(db_polku):
    tietokanta.init_database(db_polku)
    _lisaa_pisteytys(db_polku, "OLD", 9.0, paivia_sitten=40)
    _lisaa_pisteytys(db_polku, "NEW", 5.0, paivia_sitten=1)

    pisteet = tietokanta.get_latest_scores(days=30, db_path=db_polku)

    assert {p["symbol"] for p in pisteet} == {"NEW"}


def test_viimeisimmat_pisteet_rajaa_ja_jarjestaa(db_polku):
    tietokanta.init_database(db_polku)
    for symboli, pisteet in (("A", 5.0), ("B", 9.0), ("C", 7.0), ("D", 3.0)):
        _lisaa_pisteytys(db_polku, symboli, pisteet, paivia_sitten=1)

    pisteet = tietokanta.get_latest_scores(limit=3, db_path=db_polku)

    assert [p["symbol"] for p in pisteet] == ["B", "C", "A"]


def test_viimeisimmat_pisteet_tasapisteissa_uudempi_ensin(db_polku):
    tietokanta.init_database(db_polku)
    _lisaa_pisteytys(db_polku, "VANHA", 7.8, paivia_sitten=5)
    _lisaa_pisteytys(db_polku, "UUSI", 7.8, paivia_sitten=1)

    pisteet = tietokanta.get_latest_scores(db_path=db_polku)

    assert [p["symbol"] for p in pisteet] == ["UUSI", "VANHA"]


def test_viimeisimmat_pisteet_sisaltaa_pisteytyksen_ajan(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=100.0,
        db_path=db_polku,
    )

    pisteet = tietokanta.get_latest_scores(db_path=db_polku)

    assert pisteet[0]["created_at"] is not None


def test_viimeisimmat_pisteet_tyhja_kanta_palauttaa_tyhjan_listan(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_latest_scores(db_path=db_polku) == []


# --- get_transactions(): vaihe 5e (tapahtumaloki) ---


def test_tapahtumat_tyhja_kanta_palauttaa_tyhjan_listan(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_transactions(db_path=db_polku) == []


def test_tapahtumat_avoin_positio_antaa_vain_oston(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="osto1",
        db_path=db_polku,
    )

    tapahtumat = tietokanta.get_transactions(db_path=db_polku)

    assert len(tapahtumat) == 1
    osto = tapahtumat[0]
    assert osto["kind"] == "osto"
    assert osto["symbol"] == "AAPL"
    assert osto["price"] == 100.0
    assert osto["quantity"] == pytest.approx(10.0)
    assert osto["amount_usd"] == 1000.0
    assert osto["pnl_usd"] is None
    assert osto["pnl_pct"] is None
    assert osto["time"] is not None


def test_tapahtumat_suljettu_positio_antaa_myynnin_ja_oston_uusin_ensin(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="osto1",
        db_path=db_polku,
    )
    tietokanta.close_position(
        symbol="AAPL", sell_price=90.0, sell_run_id="myynti1", db_path=db_polku
    )

    tapahtumat = tietokanta.get_transactions(db_path=db_polku)

    assert [t["kind"] for t in tapahtumat] == ["myynti", "osto"]
    myynti = tapahtumat[0]
    assert myynti["price"] == 90.0
    assert myynti["amount_usd"] == pytest.approx(900.0)
    assert myynti["pnl_usd"] == pytest.approx(-100.0)
    assert myynti["pnl_pct"] == pytest.approx(-10.0)


def test_tapahtumat_perustelu_saman_ajon_ja_symbolin_paatoksesta(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="osto1",
        db_path=db_polku,
    )
    tietokanta.close_position(
        symbol="AAPL", sell_price=90.0, sell_run_id="myynti1", db_path=db_polku
    )
    for run_id, symboli, paatos, perustelu in (
        ("osto1", "AAPL", "osta", "pisteet riittivat"),
        ("osto1", "MSFT", "osta", "vaara symboli"),
        ("osto1", "AAPL", "hylatty", "vaara paatos"),
        ("myynti1", "AAPL", "myy", "stop-loss osui"),
    ):
        tietokanta.insert_decision(
            run_id=run_id,
            run_type="normaali",
            symbol=symboli,
            decision=paatos,
            rationale=perustelu,
            db_path=db_polku,
        )

    tapahtumat = tietokanta.get_transactions(db_path=db_polku)

    perustelut = {t["kind"]: t["rationale"] for t in tapahtumat}
    assert perustelut == {"osto": "pisteet riittivat", "myynti": "stop-loss osui"}


def test_tapahtumat_ilman_paatosta_perustelu_on_none(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="osto1",
        db_path=db_polku,
    )

    tapahtumat = tietokanta.get_transactions(db_path=db_polku)

    assert tapahtumat[0]["rationale"] is None


def test_tapahtumat_jarjestyvat_ajan_mukaan_uusin_ensin(db_polku):
    tietokanta.init_database(db_polku)
    for symboli in ("AAA", "BBB"):
        tietokanta.open_position(
            symbol=symboli,
            buy_price=100.0,
            stake_usd=1000.0,
            buy_run_id="osto1",
            db_path=db_polku,
        )
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            "UPDATE positions SET buy_date = '2026-10-01T10:00:00' WHERE symbol = 'BBB'"
        )
        yhteys.execute(
            "UPDATE positions SET buy_date = '2026-11-03T15:39:25' WHERE symbol = 'AAA'"
        )
        yhteys.commit()
    finally:
        yhteys.close()

    tapahtumat = tietokanta.get_transactions(db_path=db_polku)

    assert [t["symbol"] for t in tapahtumat] == ["AAA", "BBB"]


# --- skipped_symbols: insert_skip() / get_skips() (vaihe 2i) ---


def test_ohitus_tallentuu_ja_luetaan_takaisin(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_skip(
        run_id="ajo1",
        symbol="CVLT",
        stage="hintadata_puuttuu",
        reason="time_series ei palauttanut dataa",
        db_path=db_polku,
    )

    rivit = tietokanta.get_skips("ajo1", db_path=db_polku)
    assert len(rivit) == 1
    assert rivit[0]["symbol"] == "CVLT"
    assert rivit[0]["stage"] == "hintadata_puuttuu"
    assert rivit[0]["reason"] == "time_series ei palauttanut dataa"


def test_ohitukset_rajautuvat_kysyttyyn_ajoon(db_polku):
    tietokanta.init_database(db_polku)

    tietokanta.insert_skip(
        run_id="ajo1",
        symbol="CVLT",
        stage="hintadata_puuttuu",
        reason="x",
        db_path=db_polku,
    )
    tietokanta.insert_skip(
        run_id="ajo2", symbol="PG", stage="hinta_alaraja", reason="y", db_path=db_polku
    )

    rivit = tietokanta.get_skips("ajo1", db_path=db_polku)
    assert [r["symbol"] for r in rivit] == ["CVLT"]


def test_ohitukset_tyhja_kanta_palauttaa_tyhjan_listan(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_skips("ajo1", db_path=db_polku) == []


def test_init_database_lisaa_skipped_symbols_taulun_vanhaan_kantaan_ilman_migraatiota(
    db_polku,
):
    # skipped_symbols on kokonaan uusi taulu, ei uusi sarake olemassa
    # olevaan tauluun - executescript(SCHEMA) luo sen suoraan CREATE TABLE
    # IF NOT EXISTS:lla, joten sarakemigraatiota (vaihe D2) ei tarvita
    # taman taulun kohdalla. Simuloi "vanhaa" tuotantokantaa jossa taulua
    # ei viela ole ja varmistaa etta init_database() ei kaadu ja ettei se
    # raportoi taman taulun kohdalla mitaan lisattyna sarakkeena.
    yhteys = tietokanta.open_connection(db_polku)
    try:
        yhteys.execute(
            "CREATE TABLE portfolio ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "initial_capital REAL NOT NULL, cash REAL NOT NULL, "
            "currency TEXT NOT NULL DEFAULT 'USD', updated_at TEXT NOT NULL)"
        )
        yhteys.commit()
    finally:
        yhteys.close()

    lisatyt = tietokanta.init_database(db_polku)

    assert not any(nimi.startswith("skipped_symbols.") for nimi in lisatyt)
    assert tietokanta.get_skips("ajo1", db_path=db_polku) == []


# --- ajastuksen lukufunktiot (vaihe 6a) ---


def _kirjaa_ajo(polku, run_id, run_type, started_at, status):
    tietokanta.start_run(
        run_id=run_id,
        run_type=run_type,
        settings_json="{}",
        model_version="1.2",
        db_path=polku,
    )
    if status != "kesken":
        tietokanta.finish_run(run_id, status, db_path=polku)
    yhteys = tietokanta.open_connection(polku)
    try:
        yhteys.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?", (started_at, run_id)
        )
        yhteys.commit()
    finally:
        yhteys.close()


def test_get_completed_run_types_vain_valmis_ja_annettu_paiva(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    _kirjaa_ajo(polku, "a", "tarkistus", "2026-10-06T10:00:05", "valmis")
    _kirjaa_ajo(polku, "b", "tarkistus", "2026-10-06T11:00:00", "valmis")
    _kirjaa_ajo(polku, "c", "normaali", "2026-10-06T10:05:00", "keskeytyi")
    _kirjaa_ajo(polku, "d", "normaali", "2026-10-05T10:05:00", "valmis")
    _kirjaa_ajo(polku, "e", "normaali", "2026-10-06T10:09:00", "kesken")

    assert tietokanta.get_completed_run_types("2026-10-06", db_path=polku) == {
        "tarkistus"
    }
    assert tietokanta.get_completed_run_types("2026-10-05", db_path=polku) == {
        "normaali"
    }
    assert tietokanta.get_completed_run_types("2026-10-07", db_path=polku) == set()


def test_get_last_completed_run_uusin_valmis_annetulla_tyypilla(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    _kirjaa_ajo(polku, "vanha", "tarkistus", "2026-10-03T10:00:00", "valmis")
    _kirjaa_ajo(polku, "uusi", "tarkistus", "2026-10-06T10:00:00", "valmis")
    _kirjaa_ajo(polku, "kaatui", "tarkistus", "2026-10-07T10:00:00", "keskeytyi")
    _kirjaa_ajo(polku, "normaali", "normaali", "2026-10-08T10:00:00", "valmis")

    rivi = tietokanta.get_last_completed_run("tarkistus", db_path=polku)

    assert rivi["run_id"] == "uusi"


def test_get_last_completed_run_ei_yhtaan_none(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    _kirjaa_ajo(polku, "kaatui", "tarkistus", "2026-10-07T10:00:00", "keskeytyi")

    assert tietokanta.get_last_completed_run("tarkistus", db_path=polku) is None


# --- vaihe 6b: run_exists ja API-avainten peittaminen ---


def test_run_exists(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    _kirjaa_ajo(polku, "olemassa", "tarkistus", "2026-10-06T10:00:00", "valmis")

    assert tietokanta.run_exists("olemassa", db_path=polku) is True
    assert tietokanta.run_exists("tuntematon", db_path=polku) is False


def test_finish_run_peittaa_api_avaimen_kannassa_ja_arkistossa(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.start_run(
        run_id="ajo1",
        run_type="normaali",
        settings_json="{}",
        model_version="1.2",
        db_path=polku,
    )

    tietokanta.finish_run(
        "ajo1",
        "keskeytyi",
        error="401 for url: https://api.twelvedata.com/stocks?apikey=salainen123",
        db_path=polku,
    )

    virhe = tietokanta.get_run("ajo1", db_path=polku)["error"]
    assert "apikey=***" in virhe
    assert "salainen123" not in virhe
    arkisto = (tietokanta.ARKISTO_DIR / "ajo1" / "_ajo.json").read_text(
        encoding="utf-8"
    )
    assert "apikey=***" in arkisto
    assert "salainen123" not in arkisto


def test_insert_skip_peittaa_api_avaimen(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.insert_skip(
        run_id="ajo1",
        symbol="TEST",
        stage="haku_epaonnistui",
        reason="Max retries exceeded with url: /x?apikey=salainen123 (Caused by)",
        db_path=polku,
    )

    syy = tietokanta.get_skips("ajo1", db_path=polku)[0]["reason"]
    assert "apikey=*** (Caused by)" in syy
    assert "salainen123" not in syy
