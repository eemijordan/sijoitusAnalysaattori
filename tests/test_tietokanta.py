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
    assert rivit[0]["target_price"] is None
    assert rivit[0]["rr_ratio"] is None


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

    assert "AAPL" in jaahyt
    assert (
        jaahyt["AAPL"] == (tanaan - timedelta(days=10) + timedelta(days=30)).isoformat()
    )


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
    assert "AAPL" in jaahyt
    assert (
        jaahyt["AAPL"] == (tanaan - timedelta(days=5) + timedelta(days=30)).isoformat()
    )


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
    # koskaan lisaisi saraketta talleiseen tauluun - _ensure_column():n
    # pitaa tehda se ALTER TABLE:lla.
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


# --- get_recent_runs() ja get_best_scores(): vaihe 5 (dashboard) ---


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


def test_parhaat_pisteet_yksi_rivi_per_symboli_paras_voittaa(db_polku):
    tietokanta.init_database(db_polku)
    for pisteet in (5.0, 9.0, 7.0):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol="AAPL",
            risk_reward_score=pisteet,
            price=100.0,
            db_path=db_polku,
        )

    parhaat = tietokanta.get_best_scores(db_path=db_polku)

    assert len(parhaat) == 1
    assert parhaat[0]["symbol"] == "AAPL"
    assert parhaat[0]["risk_reward_score"] == 9.0


def test_parhaat_pisteet_jattaa_pois_ikkunaa_vanhemmat(db_polku):
    tietokanta.init_database(db_polku)
    yhteys = tietokanta.open_connection(db_polku)
    try:
        vanha = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
        yhteys.execute(
            """
            INSERT INTO technical_scores (
                run_id, run_type, symbol, risk_reward_score, price, created_at
            ) VALUES ('vanha_ajo', 'normaali', 'OLD', 9.0, 100.0, ?)
            """,
            (vanha,),
        )
        yhteys.commit()
    finally:
        yhteys.close()
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="NEW",
        risk_reward_score=5.0,
        price=50.0,
        db_path=db_polku,
    )

    parhaat = tietokanta.get_best_scores(days=30, db_path=db_polku)

    symbolit = {p["symbol"] for p in parhaat}
    assert "OLD" not in symbolit
    assert "NEW" in symbolit


def test_parhaat_pisteet_rajaa_ja_jarjestaa(db_polku):
    tietokanta.init_database(db_polku)
    for symboli, pisteet in (("A", 5.0), ("B", 9.0), ("C", 7.0), ("D", 3.0)):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            risk_reward_score=pisteet,
            price=100.0,
            db_path=db_polku,
        )

    parhaat = tietokanta.get_best_scores(limit=3, db_path=db_polku)

    assert [p["symbol"] for p in parhaat] == ["B", "C", "A"]


def test_parhaat_pisteet_sisaltaa_pisteytyksen_paivamaaran(db_polku):
    tietokanta.init_database(db_polku)
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=100.0,
        db_path=db_polku,
    )

    parhaat = tietokanta.get_best_scores(db_path=db_polku)

    assert parhaat[0]["created_at"] is not None


def test_parhaat_pisteet_tyhja_kanta_palauttaa_tyhjan_listan(db_polku):
    tietokanta.init_database(db_polku)

    assert tietokanta.get_best_scores(db_path=db_polku) == []
