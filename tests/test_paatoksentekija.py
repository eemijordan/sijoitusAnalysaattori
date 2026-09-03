import sqlite3

import pytest

from agents import paatoksentekija
from core import tietokanta
from core.asetukset import MAX_POSITIOT, POSITION_KOKO, RR_KYNNYS


def _pisteytys(symboli="AAPL", risk_reward_pisteet=8.0, hinta=100.0):
    return {
        "symboli": symboli,
        "risk_reward_pisteet": risk_reward_pisteet,
        "hinta": hinta,
    }


def _ehdokas(viiteostohinta=100.0, pisteet=5.0):
    return {"viiteostohinta": viiteostohinta, "pisteet": pisteet}


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
    pisteytys = _pisteytys(risk_reward_pisteet=RR_KYNNYS - 1)

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, _ehdokas(), _tila())

    assert paatos == "hylatty"
    assert "risk_reward_pisteet" in perustelu
    assert "RR_KYNNYS" in perustelu


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
    tila = _tila(aktiivisia=MAX_POSITIOT)

    paatos, perustelu = paatoksentekija.paata_osto(_pisteytys(), _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "MAX_POSITIOT" in perustelu


def test_liian_vahan_kateista_hylkaa():
    tila = _tila(kateinen=POSITION_KOKO - 1)

    paatos, perustelu = paatoksentekija.paata_osto(_pisteytys(), _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "POSITION_KOKO" in perustelu


def test_rr_tarkistetaan_ennen_kapasiteettia():
    pisteytys = _pisteytys(risk_reward_pisteet=RR_KYNNYS - 1)
    tila = _tila(aktiivisia=MAX_POSITIOT)

    paatos, perustelu = paatoksentekija.paata_osto(pisteytys, _ehdokas(), tila)

    assert paatos == "hylatty"
    assert "RR_KYNNYS" in perustelu
    assert "MAX_POSITIOT" not in perustelu


# --- aja(): tietokannan kanssa, lahtodata kirjoitettu suoraan kantaan ---


def test_aja_kapasiteetti_rajoittaa_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    for i in range(MAX_POSITIOT - 1):
        tietokanta.avaa_positio(
            symboli=f"OLD{i}",
            ostohinta=10.0,
            panos_usd=POSITION_KOKO,
            osto_ajo_id="aiempi",
            polku=polku,
        )

    for symboli in ("AAA", "BBB", "CCC"):
        tietokanta.lisaa_pisteytys(
            ajo_id="ajo1",
            ajotyyppi="normaali",
            symboli=symboli,
            risk_reward_pisteet=8.0,
            hinta=50.0,
            polku=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.hae_paatokset("ajo1", polku=polku)
    ostot = [p for p in paatokset if p["paatos"] == "osta"]
    hylatyt = [p for p in paatokset if p["paatos"] == "hylatty"]

    assert len(ostot) == 1
    assert len(hylatyt) == 2
    assert all("MAX_POSITIOT" in p["perustelu"] for p in hylatyt)


def test_aja_kateinen_rajoittaa_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    # yksi olemassa oleva positio jattaa kateiseksi 1500 mutta pitaa salkun
    # kokonaisarvon (1500 + 8500 = 10000) SALKUN_STOP_TASOn ylapuolella,
    # jotta testi mittaa nimenomaan kateisrajaa eika pysaytinta.
    tietokanta.avaa_positio(
        symboli="OLD",
        ostohinta=100.0,
        panos_usd=8500.0,
        osto_ajo_id="aiempi",
        polku=polku,
    )
    assert tietokanta.hae_kateinen(polku=polku) == 1500.0

    for symboli in ("AAA", "BBB"):
        tietokanta.lisaa_pisteytys(
            ajo_id="ajo1",
            ajotyyppi="normaali",
            symboli=symboli,
            risk_reward_pisteet=8.0,
            hinta=50.0,
            polku=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.hae_paatokset("ajo1", polku=polku)
    ostot = [p for p in paatokset if p["paatos"] == "osta"]

    assert len(ostot) == 1
    assert tietokanta.hae_kateinen(polku=polku) == 500.0


def test_aja_jarjestyssaanto_rr_sitten_ehdokkaan_pisteet_sitten_aakkoset(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    # 8 aktiivista positiota -> 2 paikkaa vapaana, jotta jarjestys ratkaisee
    # ketka kaksi neljasta ehdokkaasta ostetaan.
    for i in range(8):
        tietokanta.avaa_positio(
            symboli=f"OLD{i}",
            ostohinta=10.0,
            panos_usd=POSITION_KOKO,
            osto_ajo_id="aiempi",
            polku=polku,
        )

    for symboli, pisteet in (
        ("BETA", 10.0),
        ("ALFA", 10.0),
        ("GAMMA", 3.0),
        ("ZETA", 5.0),
    ):
        tietokanta.lisaa_ehdokas(
            ajo_id="ajo1",
            ajotyyppi="normaali",
            symboli=symboli,
            lahde="analyytikko",
            pisteet=pisteet,
            polku=polku,
        )

    for symboli, rr in (("ZETA", 9.0), ("BETA", 8.0), ("ALFA", 8.0), ("GAMMA", 8.0)):
        tietokanta.lisaa_pisteytys(
            ajo_id="ajo1",
            ajotyyppi="normaali",
            symboli=symboli,
            risk_reward_pisteet=rr,
            hinta=50.0,
            polku=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = {
        p["symboli"]: p["paatos"] for p in tietokanta.hae_paatokset("ajo1", polku=polku)
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
    tietokanta.alusta_tietokanta(polku)

    nykyinen = tietokanta.hae_kateinen(polku=polku)
    tietokanta.muuta_kateista(1000 - nykyinen, polku=polku)

    for symboli in ("AAA", "BBB"):
        tietokanta.lisaa_pisteytys(
            ajo_id="ajo1",
            ajotyyppi="normaali",
            symboli=symboli,
            risk_reward_pisteet=9.0,
            hinta=50.0,
            polku=polku,
        )

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.hae_paatokset("ajo1", polku=polku)

    assert len(paatokset) == 2
    assert all(p["paatos"] == "hylatty" for p in paatokset)
    assert all("pysaytin" in p["perustelu"] for p in paatokset)


def test_aja_nolla_pisteytysta_palauttaa_siististi(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    tulos = paatoksentekija.aja("ajo_tyhja", "normaali", polku=polku)

    assert "0 pisteytysta" in tulos
    assert tietokanta.hae_paatokset("ajo_tyhja", polku=polku) == []


def test_aja_tarkistus_ei_tee_paatoksia(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    tietokanta.lisaa_pisteytys(
        ajo_id="ajo1",
        ajotyyppi="tarkistus",
        symboli="AAPL",
        risk_reward_pisteet=9.0,
        hinta=100.0,
        polku=polku,
    )

    tulos = paatoksentekija.aja("ajo1", "tarkistus", polku=polku)

    assert tietokanta.hae_paatokset("ajo1", polku=polku) == []
    assert "vaihe 3b" in tulos


def test_aja_jokaisesta_pisteytyksesta_syntyy_tasan_yksi_paatos(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    symbolit = ["AAA", "BBB", "CCC", "DDD"]
    for symboli in symbolit:
        tietokanta.lisaa_pisteytys(
            ajo_id="ajo1",
            ajotyyppi="normaali",
            symboli=symboli,
            risk_reward_pisteet=5.0,
            hinta=50.0,
            polku=polku,
        )  # alle RR_KYNNYS -> hylatty, mutta paatos kirjataan silti

    paatoksentekija.aja("ajo1", "normaali", polku=polku)

    paatokset = tietokanta.hae_paatokset("ajo1", polku=polku)
    assert len(paatokset) == len(symbolit)
    assert {p["symboli"] for p in paatokset} == set(symbolit)


# --- tietokanta.avaa_positio() ---


def test_avaa_positio_onnistuu_ja_vahentaa_kateisen(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    alku_kateinen = tietokanta.hae_kateinen(polku=polku)

    positio_id = tietokanta.avaa_positio(
        symboli="AAPL",
        ostohinta=50.0,
        panos_usd=1000.0,
        osto_ajo_id="ajo1",
        polku=polku,
    )

    assert positio_id > 0
    assert tietokanta.hae_kateinen(polku=polku) == alku_kateinen - 1000.0

    yhteys = tietokanta.avaa_yhteys(polku)
    try:
        rivi = yhteys.execute(
            "SELECT * FROM positions WHERE id = ?", (positio_id,)
        ).fetchone()
    finally:
        yhteys.close()

    assert rivi["maara"] == 1000.0 / 50.0
    assert rivi["tila"] == "aktiivinen"


def test_avaa_positio_liian_vahan_kateista(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    with pytest.raises(ValueError):
        tietokanta.avaa_positio(
            symboli="AAPL",
            ostohinta=50.0,
            panos_usd=1_000_000.0,
            osto_ajo_id="ajo1",
            polku=polku,
        )

    yhteys = tietokanta.avaa_yhteys(polku)
    try:
        maara = yhteys.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    finally:
        yhteys.close()

    assert maara == 0


def test_avaa_positio_kaksoisosto_nostaa_integrityerrorin(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)

    tietokanta.avaa_positio(
        symboli="AAPL",
        ostohinta=50.0,
        panos_usd=1000.0,
        osto_ajo_id="ajo1",
        polku=polku,
    )
    kateinen_ekan_jalkeen = tietokanta.hae_kateinen(polku=polku)

    with pytest.raises(sqlite3.IntegrityError):
        tietokanta.avaa_positio(
            symboli="AAPL",
            ostohinta=60.0,
            panos_usd=1000.0,
            osto_ajo_id="ajo2",
            polku=polku,
        )

    assert tietokanta.hae_kateinen(polku=polku) == kateinen_ekan_jalkeen
