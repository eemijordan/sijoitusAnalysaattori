from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from agents import tekninen_analyytikko
from core import tietokanta


class _KiinteaKello:
    """Korvaa moduulin datetime-nimen niin etta datetime.now(tz) palauttaa
    kiinnitetyn hetken - testit eivat saa riippua oikeasta kellosta."""

    def __init__(self, hetki):
        self.hetki = hetki

    def now(self, tz=None):
        return self.hetki


def _paivapalkki(pvm, close=100.0, high=101.0, low=99.0, volume=1_000_000):
    return {"datetime": pvm, "open": close, "high": high, "low": low, "close": close, "volume": volume}


def _time_series_vastaus(paivat):
    return {"meta": {"symbol": "TEST"}, "values": paivat, "status": "ok"}


def _kurssidata(**kwargs):
    perus = {
        "nykyhinta": 100.0,
        "sma50": None,
        "sma200": None,
        "ylin_52vk": None,
        "alin_52vk": None,
        "historia_paivia": 10,
        "keskimaarainen_paivavaihto_usd": 40_000_000,
    }
    perus.update(kwargs)
    return perus


# --- laske_pisteet(): puhdas logiikka, ei verkkoa eika tietokantaa ---


def test_taysi_pistemaara_antaa_10():
    kurssidata = _kurssidata(
        nykyhinta=100.0, sma50=90.0, sma200=80.0, ylin_52vk=120.0, alin_52vk=60.0
    )
    # riski: atr 1.5/100=1.5% -> 3p. trendi: 100>90 ja 90>80 -> 4p.
    # asema: (100-60)/(120-60)=66.7% -> 2p. rsi 50 (40-65) -> 1p. Yht=10.
    pisteet, indikaattorit, perustelu = tekninen_analyytikko.laske_pisteet(
        kurssidata, atr=1.5, rsi=50.0
    )

    assert pisteet == 10
    assert indikaattorit["riski_pisteet"] == 3
    assert indikaattorit["trendi_pisteet"] == 4
    assert indikaattorit["asema_pisteet"] == 2
    assert indikaattorit["rsi_pisteet"] == 1


def test_matala_volatiliteetti_saa_riskista_3_korkea_0():
    kurssidata = _kurssidata(nykyhinta=100.0)

    _, matala, _ = tekninen_analyytikko.laske_pisteet(kurssidata, atr=1.5, rsi=None)
    _, korkea, _ = tekninen_analyytikko.laske_pisteet(kurssidata, atr=10.0, rsi=None)

    assert matala["riski_pisteet"] == 3
    assert korkea["riski_pisteet"] == 0


def test_hinta_sma50_alla_ei_saa_trendipisteita():
    kurssidata = _kurssidata(nykyhinta=80.0, sma50=90.0, sma200=100.0)

    _, indikaattorit, _ = tekninen_analyytikko.laske_pisteet(kurssidata, atr=None, rsi=None)

    assert indikaattorit["trendi_pisteet"] == 0


def test_alle_200_paivan_historia_jattaa_sma200_pois():
    kurssidata = _kurssidata(nykyhinta=100.0, sma50=90.0, sma200=None, historia_paivia=150)

    pisteet, indikaattorit, perustelu = tekninen_analyytikko.laske_pisteet(
        kurssidata, atr=None, rsi=None
    )

    assert indikaattorit["trendi_pisteet"] == 2  # vain hinta > sma50 -osuus
    assert "SMA200 ei laskettavissa" in perustelu
    assert "150" in perustelu


def test_asema_pisteet_52_viikon_valissa():
    def kurssidata(nykyhinta):
        return _kurssidata(nykyhinta=nykyhinta, ylin_52vk=100.0, alin_52vk=0.0)

    # kaikki muu neutraalia (atr=None, sma=None, rsi=None) -> yhteispisteet
    # tasmaavat asema_pisteet-arvoon suoraan.
    p90, _, _ = tekninen_analyytikko.laske_pisteet(kurssidata(90.0), atr=None, rsi=None)
    p60, _, _ = tekninen_analyytikko.laske_pisteet(kurssidata(60.0), atr=None, rsi=None)
    p20, _, _ = tekninen_analyytikko.laske_pisteet(kurssidata(20.0), atr=None, rsi=None)

    assert p90 == 1  # venynyt (85-100 %)
    assert p60 == 2  # 40-85 %
    assert p20 == 0  # alle 40 %


def test_rsi_80_vie_2_ja_rsi_25_vie_1():
    kurssidata = _kurssidata(nykyhinta=100.0)
    # atr 1 % -> riski 3p pohjaksi, jotta RSI-vahennys nakyy ilman
    # nollarajausta (raakasumma voi olla negatiivinen, mutta pisteet ei
    # koskaan mene alle nollan).
    p80, _, _ = tekninen_analyytikko.laske_pisteet(kurssidata, atr=1.0, rsi=80.0)
    p25, _, _ = tekninen_analyytikko.laske_pisteet(kurssidata, atr=1.0, rsi=25.0)

    assert p80 == 3 - 2  # RSI yli 75 -> -2
    assert p25 == 3 - 1  # RSI alle 30 -> -1


def test_yhteispisteet_rajautuvat_valille_0_10():
    # atr=None(0) + trendi=None(0) + asema alle 40%(0) + rsi yli 75(-2) = -2
    # raakana -> rajataan nollaan, ei negatiiviseksi. Pistemallin ylaraja on
    # rakenteellisesti 10 (3+4+2+1), joten se ei voi koskaan ylittya.
    kurssidata = _kurssidata(nykyhinta=10.0, ylin_52vk=100.0, alin_52vk=0.0)  # asema 10 % -> 0p

    pisteet, _, _ = tekninen_analyytikko.laske_pisteet(kurssidata, atr=None, rsi=80.0)

    assert pisteet == 0
    assert 0 <= pisteet <= 10


def test_puuttuva_atr_ja_rsi_eivat_kaada_laskentaa():
    kurssidata = _kurssidata(nykyhinta=100.0, sma50=90.0, sma200=80.0)

    pisteet, indikaattorit, perustelu = tekninen_analyytikko.laske_pisteet(
        kurssidata, atr=None, rsi=None
    )

    assert indikaattorit["riski_pisteet"] == 0
    assert indikaattorit["rsi_pisteet"] == 0
    assert "ATR puuttuu" in perustelu
    assert "RSI puuttuu" in perustelu
    assert pisteet == 4  # pelkka trendi (2+2)


# --- onko_palkki_valmis(): puhdas logiikka, ei kelloa eika verkkoa ---


def test_onko_palkki_valmis_eilinen_palkki():
    valmis, syy = tekninen_analyytikko.onko_palkki_valmis(date(2026, 9, 3), date(2026, 9, 4))

    assert valmis is True
    assert syy == ""


def test_onko_palkki_valmis_kuluva_paiva():
    valmis, syy = tekninen_analyytikko.onko_palkki_valmis(date(2026, 9, 4), date(2026, 9, 4))

    assert valmis is False
    assert "2026-09-04" in syy


def test_onko_palkki_valmis_tulevaisuus():
    valmis, syy = tekninen_analyytikko.onko_palkki_valmis(date(2026, 9, 5), date(2026, 9, 4))

    assert valmis is False
    assert "2026-09-05" in syy
    assert "2026-09-04" in syy


def test_onko_palkki_valmis_perjantai_ennen_maanantaita_viikonlopun_yli():
    perjantai = date(2023, 1, 6)  # perjantai
    seuraava_maanantai = date(2023, 1, 9)

    valmis, syy = tekninen_analyytikko.onko_palkki_valmis(perjantai, seuraava_maanantai)

    assert valmis is True
    assert syy == ""


# --- hae_kurssidata(): _hae_twelvedata_json monkeypatchattu, kello kiinnitetty ---


def test_hae_kurssidata_eilinen_palkki_palauttaa_kurssidatan(monkeypatch):
    tanaan = date(2026, 9, 4)
    eilen = date(2026, 9, 3)
    kiintea_nyt = datetime(tanaan.year, tanaan.month, tanaan.day, 11, 0, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(tekninen_analyytikko, "datetime", _KiinteaKello(kiintea_nyt))
    monkeypatch.setattr(tekninen_analyytikko, "hae_twelve_data_avain", lambda: "testiavain")
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_hae_twelvedata_json",
        lambda url, params: _time_series_vastaus([_paivapalkki(eilen.isoformat())]),
    )

    kurssidata = tekninen_analyytikko.hae_kurssidata("AAPL")

    assert kurssidata is not None
    assert kurssidata["viimeisin_palkki"] == eilen.isoformat()


def test_hae_kurssidata_kesken_olevalta_paivalta_nostaa_virheen(monkeypatch):
    tanaan = date(2026, 9, 4)
    kiintea_nyt = datetime(tanaan.year, tanaan.month, tanaan.day, 11, 0, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(tekninen_analyytikko, "datetime", _KiinteaKello(kiintea_nyt))
    monkeypatch.setattr(tekninen_analyytikko, "hae_twelve_data_avain", lambda: "testiavain")
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_hae_twelvedata_json",
        lambda url, params: _time_series_vastaus([_paivapalkki(tanaan.isoformat())]),
    )

    with pytest.raises(tekninen_analyytikko.KeskenerainenKurssidataVirhe):
        tekninen_analyytikko.hae_kurssidata("AAPL")


def test_hae_kurssidata_puuttuva_datetime_nostaa_virheen(monkeypatch):
    monkeypatch.setattr(tekninen_analyytikko, "hae_twelve_data_avain", lambda: "testiavain")
    rivi = _paivapalkki("2026-09-03")
    del rivi["datetime"]
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_hae_twelvedata_json",
        lambda url, params: _time_series_vastaus([rivi]),
    )

    with pytest.raises(tekninen_analyytikko.KeskenerainenKurssidataVirhe):
        tekninen_analyytikko.hae_kurssidata("AAPL")


def test_hae_kurssidata_jasentymaton_datetime_nostaa_virheen(monkeypatch):
    monkeypatch.setattr(tekninen_analyytikko, "hae_twelve_data_avain", lambda: "testiavain")
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_hae_twelvedata_json",
        lambda url, params: _time_series_vastaus([_paivapalkki("eilen")]),
    )

    with pytest.raises(tekninen_analyytikko.KeskenerainenKurssidataVirhe):
        tekninen_analyytikko.hae_kurssidata("AAPL")


# --- _laske_kurssitunnusluvut(): viimeisin_palkki mukana ---


def test_laske_kurssitunnusluvut_palauttaa_viimeisimman_palkin():
    paivat = [_paivapalkki("2026-09-03"), _paivapalkki("2026-09-02")]

    tunnusluvut = tekninen_analyytikko._laske_kurssitunnusluvut(paivat)

    assert tunnusluvut["viimeisin_palkki"] == "2026-09-03"
    assert tunnusluvut["nykyhinta"] == 100.0


# --- aja(): tietokantaan kirjoittava kerros, hakufunktiot monkeypatchattu ---


def test_aja_kirjoittaa_pisteytysrivin_oikealla_ajo_idlla_ja_hinnalla(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="AAPL", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )

    kurssidata = _kurssidata(nykyhinta=150.0, sma50=140.0, sma200=130.0, ylin_52vk=180.0, alin_52vk=100.0)

    monkeypatch.setattr(tekninen_analyytikko, "hae_kurssidata", lambda symboli: kurssidata)
    monkeypatch.setattr(tekninen_analyytikko, "hae_atr", lambda symboli: 2.0)
    monkeypatch.setattr(tekninen_analyytikko, "hae_rsi", lambda symboli: 55.0)

    tekninen_analyytikko.aja("ajo1", "normaali", polku=polku)

    rivit = tietokanta.hae_pisteytykset("ajo1", polku=polku)
    assert len(rivit) == 1
    assert rivit[0]["symboli"] == "AAPL"
    assert rivit[0]["ajo_id"] == "ajo1"
    assert rivit[0]["hinta"] == 150.0


def test_aja_ohittaa_alle_min_osakekurssin_eika_kutsu_atr_rsi(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="PENNY", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )

    kurssidata = _kurssidata(nykyhinta=2.0)

    def _ei_saa_kutsua(symboli):
        raise AssertionError("ATR/RSI-hakua ei saa tehda alle MIN_OSAKEKURSSI-hinnalla")

    monkeypatch.setattr(tekninen_analyytikko, "hae_kurssidata", lambda symboli: kurssidata)
    monkeypatch.setattr(tekninen_analyytikko, "hae_atr", _ei_saa_kutsua)
    monkeypatch.setattr(tekninen_analyytikko, "hae_rsi", _ei_saa_kutsua)

    tekninen_analyytikko.aja("ajo1", "normaali", polku=polku)

    rivit = tietokanta.hae_pisteytykset("ajo1", polku=polku)
    assert rivit == []


def test_aja_ohittaa_alle_min_paivavaihdon_eika_kutsu_atr_rsi(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="MICRO", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )

    kurssidata = _kurssidata(nykyhinta=20.0, keskimaarainen_paivavaihto_usd=800_000)

    def _ei_saa_kutsua(symboli):
        raise AssertionError("ATR/RSI-hakua ei saa tehda alle MIN_PAIVAVAIHTO-rajan")

    monkeypatch.setattr(tekninen_analyytikko, "hae_kurssidata", lambda symboli: kurssidata)
    monkeypatch.setattr(tekninen_analyytikko, "hae_atr", _ei_saa_kutsua)
    monkeypatch.setattr(tekninen_analyytikko, "hae_rsi", _ei_saa_kutsua)

    tekninen_analyytikko.aja("ajo1", "normaali", polku=polku)

    rivit = tietokanta.hae_pisteytykset("ajo1", polku=polku)
    assert rivit == []


def test_aja_paastaa_lapi_riittavan_paivavaihdon(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="BIGCO", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )

    kurssidata = _kurssidata(
        nykyhinta=100.0, sma50=90.0, sma200=80.0, ylin_52vk=120.0, alin_52vk=60.0,
        keskimaarainen_paivavaihto_usd=40_000_000,
    )

    monkeypatch.setattr(tekninen_analyytikko, "hae_kurssidata", lambda symboli: kurssidata)
    monkeypatch.setattr(tekninen_analyytikko, "hae_atr", lambda symboli: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "hae_rsi", lambda symboli: 50.0)

    tekninen_analyytikko.aja("ajo1", "normaali", polku=polku)

    rivit = tietokanta.hae_pisteytykset("ajo1", polku=polku)
    assert len(rivit) == 1
    assert rivit[0]["symboli"] == "BIGCO"


def test_loki_kertoo_kumpi_raja_hylkasi_symbolin(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="PENNY", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="MICRO", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )

    kurssidata_penny = _kurssidata(nykyhinta=2.0)
    kurssidata_micro = _kurssidata(nykyhinta=20.0, keskimaarainen_paivavaihto_usd=800_000)

    def _hae_kurssidata(symboli):
        return kurssidata_penny if symboli == "PENNY" else kurssidata_micro

    viestit = []
    monkeypatch.setattr(tekninen_analyytikko._loki, "info", lambda viesti: viestit.append(viesti))
    monkeypatch.setattr(tekninen_analyytikko, "hae_kurssidata", _hae_kurssidata)
    monkeypatch.setattr(tekninen_analyytikko, "hae_atr", lambda symboli: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "hae_rsi", lambda symboli: 50.0)

    tekninen_analyytikko.aja("ajo1", "normaali", polku=polku)

    # next(): valitaan nimenomaan hylkaysperustelu, ei esim. "viimeisin
    # palkki"-lokiriviä joka mainitsee saman symbolin.
    penny_viesti = next(v for v in viestit if "PENNY" in v and "MIN_OSAKEKURSSI" in v)
    micro_viesti = next(v for v in viestit if "MICRO" in v and "MIN_PAIVAVAIHTO" in v)

    assert "MIN_OSAKEKURSSI" in penny_viesti
    assert "MIN_PAIVAVAIHTO" in micro_viesti


def test_aja_yhden_symbolin_epaonnistuminen_ei_esta_muita(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.alusta_tietokanta(polku)
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="BAD", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )
    tietokanta.lisaa_ehdokas(
        ajo_id="ajo1", ajotyyppi="normaali", symboli="GOOD", lahde="analyytikko",
        jatkoon=1, polku=polku,
    )

    kurssidata_hyva = _kurssidata(nykyhinta=100.0, sma50=90.0, sma200=80.0, ylin_52vk=120.0, alin_52vk=60.0)

    def _hae_kurssidata(symboli):
        if symboli == "BAD":
            raise RuntimeError("simuloitu verkkovirhe")
        return kurssidata_hyva

    monkeypatch.setattr(tekninen_analyytikko, "hae_kurssidata", _hae_kurssidata)
    monkeypatch.setattr(tekninen_analyytikko, "hae_atr", lambda symboli: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "hae_rsi", lambda symboli: 50.0)

    tekninen_analyytikko.aja("ajo1", "normaali", polku=polku)

    rivit = tietokanta.hae_pisteytykset("ajo1", polku=polku)
    assert len(rivit) == 1
    assert rivit[0]["symboli"] == "GOOD"
