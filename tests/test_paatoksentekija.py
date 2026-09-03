import sqlite3

import pytest

from agents import paatoksentekija
from core import tietokanta
from core.asetukset import MAX_POSITIONS, POSITION_SIZE, RR_THRESHOLD


def _score(symbol="AAPL", risk_reward_score=8.0, price=100.0):
    return {
        "symbol": symbol,
        "risk_reward_score": risk_reward_score,
        "price": price,
    }


def _candidate(reference_price=100.0, score=5.0):
    return {"reference_price": reference_price, "score": score}


def _state(active_count=0, cash=10000.0, active_symbols=None):
    return {
        "active_count": active_count,
        "cash": cash,
        "active_symbols": active_symbols or set(),
    }


# --- decide_buy(): puhdas logiikka, ei tietokantaa eika verkkoa ---


def test_hinta_puuttuu_hylkaa():
    decision, rationale = paatoksentekija.decide_buy(
        _score(price=None), _candidate(), _state()
    )

    assert decision == "hylatty"
    assert "hinta" in rationale


def test_duplikaatti_hylkaa():
    score = _score(symbol="AAPL")
    state = _state(active_symbols={"AAPL"})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "AAPL" in rationale


def test_rr_alle_kynnyksen_hylkaa():
    score = _score(risk_reward_score=RR_THRESHOLD - 1)

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), _state())

    assert decision == "hylatty"
    assert "risk_reward_pisteet" in rationale
    assert "RR_THRESHOLD" in rationale


def test_hinta_yli_viiteostohinnan_hylkaa():
    score = _score(price=108.0)
    candidate = _candidate(reference_price=100.0)

    decision, rationale = paatoksentekija.decide_buy(score, candidate, _state())

    assert decision == "hylatty"
    assert "yli" in rationale


def test_hinta_alle_viiteostohinnan_hylkaa_eri_tekstilla():
    candidate = _candidate(reference_price=100.0)

    _, rationale_alle = paatoksentekija.decide_buy(
        _score(price=92.0), candidate, _state()
    )
    _, rationale_yli = paatoksentekija.decide_buy(
        _score(price=108.0), candidate, _state()
    )

    assert "alle" in rationale_alle
    assert "yli" in rationale_yli
    assert rationale_alle != rationale_yli


def test_hinta_pienen_poikkeaman_sisalla_paasee_lapi():
    score = _score(price=103.0)
    candidate = _candidate(reference_price=100.0)

    decision, _ = paatoksentekija.decide_buy(score, candidate, _state())

    assert decision == "osta"


def test_puuttuva_viiteostohinta_ohittaa_ehdon():
    score = _score()
    candidate = _candidate(reference_price=None)

    decision, rationale = paatoksentekija.decide_buy(score, candidate, _state())

    assert decision == "osta"
    assert "ohitettu" in rationale

    # sama patee jos ehdokasta ei loydy lainkaan
    decision_no_candidate, rationale_no_candidate = paatoksentekija.decide_buy(
        score, None, _state()
    )
    assert decision_no_candidate == "osta"
    assert "ohitettu" in rationale_no_candidate


def test_kapasiteetti_tayonna_hylkaa():
    state = _state(active_count=MAX_POSITIONS)

    decision, rationale = paatoksentekija.decide_buy(_score(), _candidate(), state)

    assert decision == "hylatty"
    assert "MAX_POSITIONS" in rationale


def test_liian_vahan_kateista_hylkaa():
    state = _state(cash=POSITION_SIZE - 1)

    decision, rationale = paatoksentekija.decide_buy(_score(), _candidate(), state)

    assert decision == "hylatty"
    assert "POSITION_SIZE" in rationale


def test_rr_tarkistetaan_ennen_kapasiteettia():
    score = _score(risk_reward_score=RR_THRESHOLD - 1)
    state = _state(active_count=MAX_POSITIONS)

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "RR_THRESHOLD" in rationale
    assert "MAX_POSITIONS" not in rationale


# --- run(): tietokannan kanssa, lahtodata kirjoitettu suoraan kantaan ---


def test_aja_kapasiteetti_rajoittaa_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    for i in range(MAX_POSITIONS - 1):
        tietokanta.open_position(
            symbol=f"OLD{i}",
            buy_price=10.0,
            stake_usd=POSITION_SIZE,
            buy_run_id="aiempi",
            db_path=polku,
        )

    for symbol in ("AAA", "BBB", "CCC"):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symbol,
            risk_reward_score=8.0,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    ostot = [d for d in decisions if d["decision"] == "osta"]
    hylatyt = [d for d in decisions if d["decision"] == "hylatty"]

    assert len(ostot) == 1
    assert len(hylatyt) == 2
    assert all("MAX_POSITIONS" in d["rationale"] for d in hylatyt)


def test_aja_kateinen_rajoittaa_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    # yksi olemassa oleva positio jattaa kateiseksi 1500 mutta pitaa salkun
    # kokonaisarvon (1500 + 8500 = 10000) PORTFOLIO_STOP_LEVELn ylapuolella,
    # jotta testi mittaa nimenomaan kateisrajaa eika pysaytinta.
    tietokanta.open_position(
        symbol="OLD",
        buy_price=100.0,
        stake_usd=8500.0,
        buy_run_id="aiempi",
        db_path=polku,
    )
    assert tietokanta.get_cash(db_path=polku) == 1500.0

    for symbol in ("AAA", "BBB"):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symbol,
            risk_reward_score=8.0,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    ostot = [d for d in decisions if d["decision"] == "osta"]

    assert len(ostot) == 1
    assert tietokanta.get_cash(db_path=polku) == 500.0


def test_aja_jarjestyssaanto_rr_sitten_ehdokkaan_pisteet_sitten_aakkoset(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    # 8 aktiivista positiota -> 2 paikkaa vapaana, jotta jarjestys ratkaisee
    # ketka kaksi neljasta ehdokkaasta ostetaan.
    for i in range(8):
        tietokanta.open_position(
            symbol=f"OLD{i}",
            buy_price=10.0,
            stake_usd=POSITION_SIZE,
            buy_run_id="aiempi",
            db_path=polku,
        )

    for symbol, score in (
        ("BETA", 10.0),
        ("ALFA", 10.0),
        ("GAMMA", 3.0),
        ("ZETA", 5.0),
    ):
        tietokanta.insert_candidate(
            run_id="ajo1",
            run_type="normaali",
            symbol=symbol,
            source="analyytikko",
            score=score,
            db_path=polku,
        )

    for symbol, rr in (("ZETA", 9.0), ("BETA", 8.0), ("ALFA", 8.0), ("GAMMA", 8.0)):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symbol,
            risk_reward_score=rr,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = {
        d["symbol"]: d["decision"]
        for d in tietokanta.get_decisions("ajo1", db_path=polku)
    }

    # ZETA: korkein RR (9) -> ensin.
    # ALFA vs BETA: sama RR (8) ja sama ehdokkaan pisteet (10) -> aakkoset
    # ratkaisevat, ALFA ennen BETAa.
    # GAMMA: sama RR (8) mutta matalin ehdokkaan pisteet (3) -> viimeinen.
    assert decisions["ZETA"] == "osta"
    assert decisions["ALFA"] == "osta"
    assert decisions["BETA"] == "hylatty"
    assert decisions["GAMMA"] == "hylatty"


def test_aja_salkun_stop_taso_estaa_kaikki_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    nykyinen = tietokanta.get_cash(db_path=polku)
    tietokanta.adjust_cash(1000 - nykyinen, db_path=polku)

    for symbol in ("AAA", "BBB"):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symbol,
            risk_reward_score=9.0,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)

    assert len(decisions) == 2
    assert all(d["decision"] == "hylatty" for d in decisions)
    assert all("pysaytin" in d["rationale"] for d in decisions)


def test_aja_nolla_pisteytysta_palauttaa_siististi(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tulos = paatoksentekija.run("ajo_tyhja", "normaali", db_path=polku)

    assert "0 pisteytysta" in tulos
    assert tietokanta.get_decisions("ajo_tyhja", db_path=polku) == []


def test_aja_tarkistus_ei_tee_paatoksia(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="AAPL",
        risk_reward_score=9.0,
        price=100.0,
        db_path=polku,
    )

    tulos = paatoksentekija.run("ajo1", "tarkistus", db_path=polku)

    assert tietokanta.get_decisions("ajo1", db_path=polku) == []
    assert "vaihe 3b" in tulos


def test_aja_jokaisesta_pisteytyksesta_syntyy_tasan_yksi_paatos(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    symbolit = ["AAA", "BBB", "CCC", "DDD"]
    for symbol in symbolit:
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symbol,
            risk_reward_score=5.0,
            price=50.0,
            db_path=polku,
        )  # alle RR_THRESHOLD -> hylatty, mutta paatos kirjataan silti

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    assert len(decisions) == len(symbolit)
    assert {d["symbol"] for d in decisions} == set(symbolit)


# --- tietokanta.open_position() ---


def test_avaa_positio_onnistuu_ja_vahentaa_kateisen(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    alku_kateinen = tietokanta.get_cash(db_path=polku)

    positio_id = tietokanta.open_position(
        symbol="AAPL",
        buy_price=50.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )

    assert positio_id > 0
    assert tietokanta.get_cash(db_path=polku) == alku_kateinen - 1000.0

    yhteys = tietokanta.open_connection(polku)
    try:
        rivi = yhteys.execute(
            "SELECT * FROM positions WHERE id = ?", (positio_id,)
        ).fetchone()
    finally:
        yhteys.close()

    assert rivi["quantity"] == 1000.0 / 50.0
    assert rivi["status"] == "aktiivinen"


def test_avaa_positio_liian_vahan_kateista(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    with pytest.raises(ValueError):
        tietokanta.open_position(
            symbol="AAPL",
            buy_price=50.0,
            stake_usd=1_000_000.0,
            buy_run_id="ajo1",
            db_path=polku,
        )

    yhteys = tietokanta.open_connection(polku)
    try:
        maara = yhteys.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    finally:
        yhteys.close()

    assert maara == 0


def test_avaa_positio_kaksoisosto_nostaa_integrityerrorin(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=50.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )
    kateinen_ekan_jalkeen = tietokanta.get_cash(db_path=polku)

    with pytest.raises(sqlite3.IntegrityError):
        tietokanta.open_position(
            symbol="AAPL",
            buy_price=60.0,
            stake_usd=1000.0,
            buy_run_id="ajo2",
            db_path=polku,
        )

    assert tietokanta.get_cash(db_path=polku) == kateinen_ekan_jalkeen
