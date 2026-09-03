import sqlite3

import pytest

from agents import paatoksentekija
from core import tietokanta
from core.asetukset import MAX_POSITIONS, POSITION_SIZE, RR_THRESHOLD


def _pisteytys(symboli="AAPL", risk_reward_pisteet=8.0, hinta=100.0):
    return {
        "symbol": symboli,
        "risk_reward_score": risk_reward_pisteet,
        "price": hinta,
    }


def _ehdokas(viiteostohinta=100.0, pisteet=5.0):
    return {"reference_price": viiteostohinta, "score": pisteet}


def _tila(aktiivisia=0, kateinen=10000.0, aktiiviset_symbolit=None):
    return {
        "aktiivisia": aktiivisia,
        "kateinen": kateinen,
        "aktiiviset_symbolit": aktiiviset_symbolit or set(),
    }


# --- paata_osto(): puhdas logiikka, ei tietokantaa eika verkkoa ---


def test_hinta_puuttuu_hylkaa():
    paatos, perustelu = paatoksentekija.paata_osto(
        _pisteytys(hinta=None), _ehdokas(), _tila()
    )

    assert paatos == "hylatty"
    assert "hinta" in perustelu


def test_duplikaatti_hylkaa():
    pisteytys = _pisteytys(symboli="AAPL")
    tila = _tila(aktiiviset_symbolit={"AAPL"})

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "AAPL" in perustelu


def test_rr_alle_kynnyksen_hylkaa():
    pisteytys = _pisteytys(risk_reward_pisteet=RR_THRESHOLD - 1)

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, _ehdokas(), _tila())

    assert paatos == "hylatty"
    assert "risk_reward_pisteet" in perustelu
    assert "RR_THRESHOLD" in perustelu


def test_hinta_yli_viiteostohinnan_hylkaa():
    pisteytys = _pisteytys(hinta=108.0)
    ehdokas = _ehdokas(viiteostohinta=100.0)

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, ehdokas, _tila())

    assert paatos == "hylatty"
    assert "yli" in perustelu


def test_hinta_alle_viiteostohinnan_hylkaa_eri_tekstilla():
    ehdokas = _ehdokas(viiteostohinta=100.0)

    _, perustelu_alle = paatoksentekija.paata_osto(
        _pisteytys(hinta=92.0), ehdokas, _tila()
    )
    _, perustelu_yli = paatoksentekija.paata_osto(
        _pisteytys(hinta=108.0), ehdokas, _tila()
    )

    assert "alle" in perustelu_alle
    assert "yli" in perustelu_yli
    assert perustelu_alle != perustelu_yli


def test_hinta_pienen_poikkeaman_sisalla_paasee_lapi():
    pisteytys = _pisteytys(hinta=103.0)
    ehdokas = _ehdokas(viiteostohinta=100.0)

    paatos, _ = paatoksentekija.paata_osto(pisteytys, ehdokas, _tila())

    assert paatos == "osta"


def test_puuttuva_viiteostohinta_ohittaa_ehdon():
    pisteytys = _pisteytys()
    ehdokas = _ehdokas(viiteostohinta=None)

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, ehdokas, _tila())

    assert paatos == "osta"
    assert "ohitettu" in perustelu

    # sama patee jos ehdokasta ei loydy lainkaan
    paatos_ei_ehdokasta, perustelu_ei_ehdokasta = paatoksentekija.paata_osto(
        pisteytys, None, _tila()
    )
    assert paatos_ei_ehdokasta == "osta"
    assert "ohitettu" in perustelu_ei_ehdokasta


def test_kapasiteetti_tayonna_hylkaa():
    tila = _tila(aktiivisia=MAX_POSITIONS)

    paatos, perustelu = paatoksentekija.paata_osto(_pisteytys(), _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "MAX_POSITIONS" in perustelu


def test_liian_vahan_kateista_hylkaa():
    tila = _tila(kateinen=POSITION_SIZE - 1)

    paatos, perustelu = paatoksentekija.paata_osto(_pisteytys(), _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "POSITION_SIZE" in perustelu


def test_rr_tarkistetaan_ennen_kapasiteettia():
    pisteytys = _pisteytys(risk_reward_pisteet=RR_THRESHOLD - 1)
    tila = _tila(aktiivisia=MAX_POSITIONS)

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "RR_THRESHOLD" in perustelu
    assert "MAX_POSITIONS" not in perustelu


# --- aja(): tietokannan kanssa, lahtodata kirjoitettu suoraan kantaan ---


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

    for symboli in ("AAA", "BBB", "CCC"):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            risk_reward_score=8.0,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.get_decisions("ajo1", db_path=polku)
    ostot = [p for p in paatokset if p["decision"] == "osta"]
    hylatyt = [p for p in paatokset if p["decision"] == "hylatty"]

    assert len(ostot) == 1
    assert len(hylatyt) == 2
    assert all("MAX_POSITIONS" in p["rationale"] for p in hylatyt)


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

    for symboli in ("AAA", "BBB"):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            risk_reward_score=8.0,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.get_decisions("ajo1", db_path=polku)
    ostot = [p for p in paatokset if p["decision"] == "osta"]

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

    for symboli, pisteet in (
        ("BETA", 10.0),
        ("ALFA", 10.0),
        ("GAMMA", 3.0),
        ("ZETA", 5.0),
    ):
        tietokanta.insert_candidate(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            source="analyytikko",
            score=pisteet,
            db_path=polku,
        )

    for symboli, rr in (("ZETA", 9.0), ("BETA", 8.0), ("ALFA", 8.0), ("GAMMA", 8.0)):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            risk_reward_score=rr,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = {
        p["symbol"]: p["decision"]
        for p in tietokanta.get_decisions("ajo1", db_path=polku)
    }

    # ZETA: korkein RR (9) -> ensin.
    # ALFA vs BETA: sama RR (8) ja sama ehdokkaan pisteet (10) -> aakkoset
    # ratkaisevat, ALFA ennen BETAa.
    # GAMMA: sama RR (8) mutta matalin ehdokkaan pisteet (3) -> viimeinen.
    assert paatokset["ZETA"] == "osta"
    assert paatokset["ALFA"] == "osta"
    assert paatokset["BETA"] == "hylatty"
    assert paatokset["GAMMA"] == "hylatty"


def test_aja_salkun_stop_taso_estaa_kaikki_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    nykyinen = tietokanta.get_cash(db_path=polku)
    tietokanta.adjust_cash(1000 - nykyinen, db_path=polku)

    for symboli in ("AAA", "BBB"):
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            risk_reward_score=9.0,
            price=50.0,
            db_path=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.get_decisions("ajo1", db_path=polku)

    assert len(paatokset) == 2
    assert all(p["decision"] == "hylatty" for p in paatokset)
    assert all("pysaytin" in p["rationale"] for p in paatokset)


def test_aja_nolla_pisteytysta_palauttaa_siististi(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tulos = paatoksentekija.aja("ajo_tyhja", "normaali", polku=polku)

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

    tulos = paatoksentekija.aja("ajo1", "tarkistus", polku=polku)

    assert tietokanta.get_decisions("ajo1", db_path=polku) == []
    assert "vaihe 3b" in tulos


def test_aja_jokaisesta_pisteytyksesta_syntyy_tasan_yksi_paatos(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    symbolit = ["AAA", "BBB", "CCC", "DDD"]
    for symboli in symbolit:
        tietokanta.insert_score(
            run_id="ajo1",
            run_type="normaali",
            symbol=symboli,
            risk_reward_score=5.0,
            price=50.0,
            db_path=polku,
        )  # alle RR_THRESHOLD -> hylatty, mutta paatos kirjataan silti

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.get_decisions("ajo1", db_path=polku)
    assert len(paatokset) == len(symbolit)
    assert {p["symbol"] for p in paatokset} == set(symbolit)


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
