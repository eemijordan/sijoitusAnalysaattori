from agents import raportoija
from core import tietokanta
from core.asetukset import BENCHMARK_SYMBOL


def _kirjaa_viestit(monkeypatch):
    viestit = []
    monkeypatch.setattr(raportoija._loki, "info", lambda v: viestit.append(v))
    monkeypatch.setattr(raportoija._loki, "warning", lambda v: viestit.append(v))
    return viestit


# --- _record_snapshot() / run("tarkistus"): positioiden arvostus ---


def test_positioiden_arvo_lasketaan_taman_ajon_tuoreilla_hinnoilla(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        db_path=polku,
    )  # quantity = 10
    tietokanta.open_position(
        symbol="NVDA",
        buy_price=200.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        db_path=polku,
    )  # quantity = 5

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=110.0,
        db_path=polku,
    )
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="NVDA",
        risk_reward_score=8.0,
        price=200.0,
        db_path=polku,
    )

    raportoija.run("ajo1", "tarkistus", db_path=polku)

    rivit = tietokanta.get_snapshots(db_path=polku)
    assert len(rivit) == 1
    # AAPL: 10 * 110 = 1100, NVDA: 5 * 200 = 1000 -> yhteensa 2100
    assert rivit[0]["positions_value"] == 2100.0


def test_positio_ilman_taman_ajon_hintaa_kayttaa_viimeisinta_aiempaa(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        db_path=polku,
    )  # quantity = 10
    # vanha pisteytys edelliselta ajolta, ei rivia taman ajon (ajo2) alta
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=120.0,
        db_path=polku,
    )

    raportoija.run("ajo2", "tarkistus", db_path=polku)

    rivit = tietokanta.get_snapshots(db_path=polku)
    assert rivit[0]["positions_value"] == 1200.0  # 10 * 120 (vanha hinta)


def test_positio_ilman_yhtaan_pisteytysta_kayttaa_ostohintaa_ja_lasketaan_yhteenvetoon(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    viestit = _kirjaa_viestit(monkeypatch)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        db_path=polku,
    )  # quantity = 10, ei pisteytysta lainkaan koskaan

    raportoija.run("ajo1", "tarkistus", db_path=polku)

    rivit = tietokanta.get_snapshots(db_path=polku)
    assert rivit[0]["positions_value"] == 1000.0  # 10 * buy_price 100

    assert any("1" in v and "vanhalla tai ostohinnalla" in v for v in viestit)


def test_nolla_avointa_positiota_tilannekuva_on_pelkkaa_kateista(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    raportoija.run("ajo1", "tarkistus", db_path=polku)

    rivit = tietokanta.get_snapshots(db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["positions_value"] == 0.0
    assert rivit[0]["total_value"] == rivit[0]["cash"]
    assert rivit[0]["cash"] == tietokanta.get_cash(db_path=polku)


# --- tuottolaskenta ---


def test_ensimmainen_tilannekuva_kertoo_etta_vertailu_alkaa_tasta(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    viestit = _kirjaa_viestit(monkeypatch)

    tietokanta.insert_market_reference(
        run_id="ajo1", symbol=BENCHMARK_SYMBOL, price=500.0, db_path=polku
    )

    raportoija.run("ajo1", "tarkistus", db_path=polku)

    assert any("vertailu alkaa tasta ajosta" in v for v in viestit)


def test_toinen_tilannekuva_laskee_tuoton_ja_eron_spyhyn(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    viestit = _kirjaa_viestit(monkeypatch)

    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-09-01",
        cash=10000.0,
        positions_value=0.0,
        total_value=10000.0,
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_price=500.0,
        db_path=polku,
    )

    # kateinen 10200, ei positioita -> total_value = 10200
    tietokanta.adjust_cash(200.0, db_path=polku)
    tietokanta.insert_market_reference(
        run_id="ajo2", symbol=BENCHMARK_SYMBOL, price=505.0, db_path=polku
    )

    raportoija.run("ajo2", "tarkistus", db_path=polku)

    rivit = tietokanta.get_snapshots(db_path=polku)
    assert len(rivit) == 2
    assert rivit[1]["total_value"] == 10200.0

    viesti = next(v for v in viestit if "tuotto" in v)
    assert "+2.00 %" in viesti  # salkun tuotto (10200/10000-1)*100
    assert "+1.00 %" in viesti  # SPY-tuotto (505/500-1)*100
    assert "+1.00 %-yks" in viesti  # ero


def test_lahtotilannekuva_ilman_vertailukurssia_ohitetaan(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    viestit = _kirjaa_viestit(monkeypatch)

    # ensimmainen tilannekuva ilman benchmark_price:a - ei kelpaa lahtopisteeksi
    tietokanta.insert_snapshot(
        run_id="ajo1",
        snapshot_date="2026-08-30",
        cash=9000.0,
        positions_value=0.0,
        total_value=9000.0,
        db_path=polku,
    )
    # toinen, jolla molemmat - tama on oikea lahtopiste
    tietokanta.insert_snapshot(
        run_id="ajo2",
        snapshot_date="2026-09-01",
        cash=10000.0,
        positions_value=0.0,
        total_value=10000.0,
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_price=500.0,
        db_path=polku,
    )

    tietokanta.adjust_cash(200.0, db_path=polku)
    tietokanta.insert_market_reference(
        run_id="ajo3", symbol=BENCHMARK_SYMBOL, price=505.0, db_path=polku
    )

    raportoija.run("ajo3", "tarkistus", db_path=polku)

    viesti = next(v for v in viestit if "tuotto" in v)
    # jos 9000 (vaarat perusteet) olisi kaytetty lahtona, tuotto olisi
    # (10200/9000-1)*100 = +13.33 %, ei +2.00 %
    assert "+2.00 %" in viesti


# --- regressio: normaaliajo ei muutu ---


def test_normaaliajo_yhteenveto_ennallaan_eika_tilannekuvaa_synny(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        source="analyytikko",
        score=5.0,
        db_path=polku,
    )
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=100.0,
        db_path=polku,
    )
    tietokanta.insert_decision(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        decision="osta",
        db_path=polku,
    )

    summary = raportoija.run("ajo1", "normaali", db_path=polku)

    assert summary == "ajo ajo1 (normaali): 1 ehdokasta, 1 pisteytysta, 1 paatosta"
    assert tietokanta.get_snapshots(db_path=polku) == []
