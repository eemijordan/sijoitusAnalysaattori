from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import requests

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
    return {
        "datetime": pvm,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _time_series_vastaus(paivat):
    return {"meta": {"symbol": "TEST"}, "values": paivat, "status": "ok"}


def _price_data(**kwargs):
    perus = {
        "current_price": 100.0,
        "sma50": None,
        "sma200": None,
        "high_52w": None,
        "low_52w": None,
        "history_days": 10,
        "avg_daily_turnover_usd": 40_000_000,
    }
    perus.update(kwargs)
    return perus


# --- calculate_score(): puhdas logiikka, ei verkkoa eika tietokantaa ---


def test_taysi_pistemaara_antaa_10():
    price_data = _price_data(
        current_price=100.0, sma50=90.0, sma200=80.0, high_52w=120.0, low_52w=60.0
    )
    # riski: atr 1.5/100=1.5% -> 3p. trendi: 100>90 ja 90>80 -> 4p.
    # asema: (100-60)/(120-60)=66.7% -> 2p. rsi 50 (40-65) -> 1p. Yht=10.
    score, indicators, rationale = tekninen_analyytikko.calculate_score(
        price_data, atr=1.5, rsi=50.0
    )

    assert score == 10
    assert indicators["risk_score"] == 3
    assert indicators["trend_score"] == 4
    assert indicators["position_score"] == 2
    assert indicators["rsi_score"] == 1


def test_regressio_calculate_score_ei_muutu_stop_ja_tavoite_lisayksessa():
    # Vaihe 2g lisasi calculate_stop_and_target()-funktion mutta calculate_score()
    # itse ei saa muuttua: sama syote -> tasmalleen sama pistemaara, indikaattorit
    # ja perustelu kuin ennen tata vaihetta, eika stop/tavoite kuulu tahan paluuarvoon.
    price_data = _price_data(
        current_price=100.0, sma50=90.0, sma200=80.0, high_52w=120.0, low_52w=60.0
    )
    score, indicators, rationale = tekninen_analyytikko.calculate_score(
        price_data, atr=1.5, rsi=50.0
    )

    assert score == 10
    assert indicators["risk_score"] == 3
    assert indicators["trend_score"] == 4
    assert indicators["position_score"] == 2
    assert indicators["rsi_score"] == 1
    assert "stop_loss" not in indicators
    assert "target_price" not in indicators
    assert "rr_ratio" not in indicators


def test_matala_volatiliteetti_saa_riskista_3_korkea_0():
    price_data = _price_data(current_price=100.0)

    _, matala, _ = tekninen_analyytikko.calculate_score(price_data, atr=1.5, rsi=None)
    _, korkea, _ = tekninen_analyytikko.calculate_score(price_data, atr=10.0, rsi=None)

    assert matala["risk_score"] == 3
    assert korkea["risk_score"] == 0


def test_hinta_sma50_alla_ei_saa_trendipisteita():
    price_data = _price_data(current_price=80.0, sma50=90.0, sma200=100.0)

    _, indicators, _ = tekninen_analyytikko.calculate_score(
        price_data, atr=None, rsi=None
    )

    assert indicators["trend_score"] == 0


def test_alle_200_paivan_historia_jattaa_sma200_pois():
    price_data = _price_data(
        current_price=100.0, sma50=90.0, sma200=None, history_days=150
    )

    score, indicators, rationale = tekninen_analyytikko.calculate_score(
        price_data, atr=None, rsi=None
    )

    assert indicators["trend_score"] == 2  # vain hinta > sma50 -osuus
    assert "SMA200 ei laskettavissa" in rationale
    assert "150" in rationale


def test_asema_pisteet_52_viikon_valissa():
    def price_data(current_price):
        return _price_data(current_price=current_price, high_52w=100.0, low_52w=0.0)

    # kaikki muu neutraalia (atr=None, sma=None, rsi=None) -> yhteispisteet
    # tasmaavat position_score-arvoon suoraan.
    p90, _, _ = tekninen_analyytikko.calculate_score(
        price_data(90.0), atr=None, rsi=None
    )
    p60, _, _ = tekninen_analyytikko.calculate_score(
        price_data(60.0), atr=None, rsi=None
    )
    p20, _, _ = tekninen_analyytikko.calculate_score(
        price_data(20.0), atr=None, rsi=None
    )

    assert p90 == 1  # venynyt (85-100 %)
    assert p60 == 2  # 40-85 %
    assert p20 == 0  # alle 40 %


def test_rsi_80_vie_2_ja_rsi_25_vie_1():
    price_data = _price_data(current_price=100.0)
    # atr 1 % -> riski 3p pohjaksi, jotta RSI-vahennys nakyy ilman
    # nollarajausta (raakasumma voi olla negatiivinen, mutta pisteet ei
    # koskaan mene alle nollan).
    p80, _, _ = tekninen_analyytikko.calculate_score(price_data, atr=1.0, rsi=80.0)
    p25, _, _ = tekninen_analyytikko.calculate_score(price_data, atr=1.0, rsi=25.0)

    assert p80 == 3 - 2  # RSI yli 75 -> -2
    assert p25 == 3 - 1  # RSI alle 30 -> -1


def test_yhteispisteet_rajautuvat_valille_0_10():
    # atr=None(0) + trendi=None(0) + asema alle 40%(0) + rsi yli 75(-2) = -2
    # raakana -> rajataan nollaan, ei negatiiviseksi. Pistemallin ylaraja on
    # rakenteellisesti 10 (3+4+2+1), joten se ei voi koskaan ylittya.
    price_data = _price_data(
        current_price=10.0, high_52w=100.0, low_52w=0.0
    )  # asema 10 % -> 0p

    score, _, _ = tekninen_analyytikko.calculate_score(price_data, atr=None, rsi=80.0)

    assert score == 0
    assert 0 <= score <= 10


def test_puuttuva_atr_ja_rsi_eivat_kaada_laskentaa():
    price_data = _price_data(current_price=100.0, sma50=90.0, sma200=80.0)

    score, indicators, rationale = tekninen_analyytikko.calculate_score(
        price_data, atr=None, rsi=None
    )

    assert indicators["risk_score"] == 0
    assert indicators["rsi_score"] == 0
    assert "ATR puuttuu" in rationale
    assert "RSI puuttuu" in rationale
    assert score == 4  # pelkka trendi (2+2)


# --- is_bar_ready(): puhdas logiikka, ei kelloa eika verkkoa ---


def test_onko_palkki_valmis_eilinen_palkki():
    valmis, syy = tekninen_analyytikko.is_bar_ready(date(2026, 9, 3), date(2026, 9, 4))

    assert valmis is True
    assert syy == ""


def test_onko_palkki_valmis_kuluva_paiva():
    valmis, syy = tekninen_analyytikko.is_bar_ready(date(2026, 9, 4), date(2026, 9, 4))

    assert valmis is False
    assert "2026-09-04" in syy


def test_onko_palkki_valmis_tulevaisuus():
    valmis, syy = tekninen_analyytikko.is_bar_ready(date(2026, 9, 5), date(2026, 9, 4))

    assert valmis is False
    assert "2026-09-05" in syy
    assert "2026-09-04" in syy


def test_onko_palkki_valmis_perjantai_ennen_maanantaita_viikonlopun_yli():
    perjantai = date(2023, 1, 6)  # perjantai
    seuraava_maanantai = date(2023, 1, 9)

    valmis, syy = tekninen_analyytikko.is_bar_ready(perjantai, seuraava_maanantai)

    assert valmis is True
    assert syy == ""


# --- fetch_price_data(): _fetch_twelvedata_json monkeypatchattu, kello kiinnitetty ---


def test_hae_kurssidata_eilinen_palkki_palauttaa_kurssidatan(monkeypatch):
    tanaan = date(2026, 9, 4)
    eilen = date(2026, 9, 3)
    kiintea_nyt = datetime(
        tanaan.year,
        tanaan.month,
        tanaan.day,
        11,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )

    monkeypatch.setattr(tekninen_analyytikko, "datetime", _KiinteaKello(kiintea_nyt))
    monkeypatch.setattr(
        tekninen_analyytikko, "get_twelve_data_key", lambda: "testiavain"
    )
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_fetch_twelvedata_json",
        lambda url, params: _time_series_vastaus([_paivapalkki(eilen.isoformat())]),
    )

    price_data = tekninen_analyytikko.fetch_price_data("AAPL")

    assert price_data is not None
    assert price_data["latest_bar"] == eilen.isoformat()


def test_hae_kurssidata_kesken_olevalta_paivalta_nostaa_virheen(monkeypatch):
    tanaan = date(2026, 9, 4)
    kiintea_nyt = datetime(
        tanaan.year,
        tanaan.month,
        tanaan.day,
        11,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )

    monkeypatch.setattr(tekninen_analyytikko, "datetime", _KiinteaKello(kiintea_nyt))
    monkeypatch.setattr(
        tekninen_analyytikko, "get_twelve_data_key", lambda: "testiavain"
    )
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_fetch_twelvedata_json",
        lambda url, params: _time_series_vastaus([_paivapalkki(tanaan.isoformat())]),
    )

    with pytest.raises(tekninen_analyytikko.IncompletePriceDataError):
        tekninen_analyytikko.fetch_price_data("AAPL")


def test_hae_kurssidata_puuttuva_datetime_nostaa_virheen(monkeypatch):
    monkeypatch.setattr(
        tekninen_analyytikko, "get_twelve_data_key", lambda: "testiavain"
    )
    rivi = _paivapalkki("2026-09-03")
    del rivi["datetime"]
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_fetch_twelvedata_json",
        lambda url, params: _time_series_vastaus([rivi]),
    )

    with pytest.raises(tekninen_analyytikko.IncompletePriceDataError):
        tekninen_analyytikko.fetch_price_data("AAPL")


def test_hae_kurssidata_jasentymaton_datetime_nostaa_virheen(monkeypatch):
    monkeypatch.setattr(
        tekninen_analyytikko, "get_twelve_data_key", lambda: "testiavain"
    )
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_fetch_twelvedata_json",
        lambda url, params: _time_series_vastaus([_paivapalkki("eilen")]),
    )

    with pytest.raises(tekninen_analyytikko.IncompletePriceDataError):
        tekninen_analyytikko.fetch_price_data("AAPL")


# --- _calculate_price_stats(): latest_bar mukana ---


def test_laske_kurssitunnusluvut_palauttaa_viimeisimman_palkin():
    paivat = [_paivapalkki("2026-09-03"), _paivapalkki("2026-09-02")]

    tunnusluvut = tekninen_analyytikko._calculate_price_stats(paivat)

    assert tunnusluvut["latest_bar"] == "2026-09-03"
    assert tunnusluvut["current_price"] == 100.0


# --- calculate_stop_and_target(): puhdas logiikka, ei verkkoa eika tietokantaa ---


def test_stop_tavoite_ja_rr_esimerkkilaskelma():
    stop, tavoite, rr = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=5.0, high_52w=130.0
    )

    assert stop == 90.0
    assert tavoite == 130.0
    assert rr == 3.0


def test_puuttuva_atr_jattaa_stopin_ja_rr_n_laskematta_tavoite_lasketaan_silti():
    stop, tavoite, rr = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=None, high_52w=130.0
    )

    assert stop is None
    assert rr is None
    assert tavoite == 130.0


def test_huippu_hinnan_alla_tai_yhtasuuri_jattaa_tavoitteen_ja_rr_n_laskematta():
    stop_alle, tavoite_alle, rr_alle = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=5.0, high_52w=90.0
    )
    stop_yhtasuuri, tavoite_yhtasuuri, rr_yhtasuuri = (
        tekninen_analyytikko.calculate_stop_and_target(
            price=100.0, atr=5.0, high_52w=100.0
        )
    )

    assert tavoite_alle is None
    assert rr_alle is None
    assert stop_alle == 90.0  # stop lasketaan silti

    assert tavoite_yhtasuuri is None
    assert rr_yhtasuuri is None
    assert stop_yhtasuuri == 90.0


def test_puuttuva_huippu_jattaa_tavoitteen_laskematta():
    _, tavoite, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=5.0, high_52w=None
    )

    assert tavoite is None


def test_valtava_atr_jolla_stop_menisi_nollaan_tai_alle_jattaa_stopin_laskematta():
    stop_nolla, _, rr_nolla = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=50.0, high_52w=130.0
    )
    stop_negatiivinen, _, rr_negatiivinen = (
        tekninen_analyytikko.calculate_stop_and_target(
            price=100.0, atr=60.0, high_52w=130.0
        )
    )

    assert stop_nolla is None
    assert rr_nolla is None
    assert stop_negatiivinen is None
    assert rr_negatiivinen is None


def test_eri_atr_antaa_eri_rr_suhteen_samalla_hinnalla_ja_huipulla():
    # Todistaa ettei rr_ratio ole vakio: jos stop ja tavoite tulisivat
    # molemmat kiinteista ATR-kertoimista, suhde olisi aina sama luku.
    _, _, rr_matala_riski = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=2.0, high_52w=130.0
    )
    _, _, rr_korkea_riski = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, atr=10.0, high_52w=130.0
    )

    assert rr_matala_riski != rr_korkea_riski


# --- _fetch_twelvedata_json(): tilapaisten verkkovirheiden uudelleenyritys ---


class _VastausTynkka:
    """Korvaa requests.Response:in testeissa."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


def test_hae_json_aikakatkaisu_ensin_sitten_onnistuu(monkeypatch):
    kutsut = []

    def _fake_get(url, params=None, timeout=None):
        kutsut.append(timeout)
        if len(kutsut) == 1:
            raise requests.exceptions.Timeout("read timed out")
        return _VastausTynkka(json_data={"values": [{"rsi": "50"}]})

    monkeypatch.setattr(tekninen_analyytikko.requests, "get", _fake_get)
    monkeypatch.setattr(tekninen_analyytikko.time, "sleep", lambda s: None)

    data = tekninen_analyytikko._fetch_twelvedata_json("url", {"symbol": "AAPL"})

    assert data == {"values": [{"rsi": "50"}]}
    assert len(kutsut) == 2


def test_hae_json_yhteysvirhe_ensin_sitten_onnistuu(monkeypatch):
    kutsut = []

    def _fake_get(url, params=None, timeout=None):
        kutsut.append(timeout)
        if len(kutsut) == 1:
            raise requests.exceptions.ConnectionError("connection reset")
        return _VastausTynkka(json_data={"values": [{"atr": "1.5"}]})

    monkeypatch.setattr(tekninen_analyytikko.requests, "get", _fake_get)
    monkeypatch.setattr(tekninen_analyytikko.time, "sleep", lambda s: None)

    data = tekninen_analyytikko._fetch_twelvedata_json("url", {"symbol": "AAPL"})

    assert data == {"values": [{"atr": "1.5"}]}
    assert len(kutsut) == 2


def test_hae_json_molemmat_yritykset_aikakatkeavat_nostaa_poikkeuksen(monkeypatch):
    kutsut = []

    def _fake_get(url, params=None, timeout=None):
        kutsut.append(timeout)
        raise requests.exceptions.Timeout("read timed out")

    nukutut = []
    monkeypatch.setattr(tekninen_analyytikko.requests, "get", _fake_get)
    monkeypatch.setattr(tekninen_analyytikko.time, "sleep", lambda s: nukutut.append(s))

    with pytest.raises(requests.exceptions.Timeout):
        tekninen_analyytikko._fetch_twelvedata_json("url", {"symbol": "AAPL"})

    assert len(kutsut) == 2
    # Odotetaan uudelleenyrityksen valissa JA ennen kuin poikkeus nousee
    # ylospain, koska myos epaonnistunut kutsu kasvattaa krediittilaskuria.
    assert nukutut == [
        tekninen_analyytikko.SYNC_SECONDS,
        tekninen_analyytikko.SYNC_SECONDS,
    ]


def test_hae_json_http_404_ei_yrita_uudelleen(monkeypatch):
    kutsut = []

    def _fake_get(url, params=None, timeout=None):
        kutsut.append(timeout)
        return _VastausTynkka(status_code=404, json_data={})

    monkeypatch.setattr(tekninen_analyytikko.requests, "get", _fake_get)
    monkeypatch.setattr(tekninen_analyytikko.time, "sleep", lambda s: None)

    with pytest.raises(requests.exceptions.HTTPError):
        tekninen_analyytikko._fetch_twelvedata_json("url", {"symbol": "AAPL"})

    assert len(kutsut) == 1


def test_hae_json_429_polku_toimii_ennallaan(monkeypatch):
    kutsut = []

    def _fake_get(url, params=None, timeout=None):
        kutsut.append(timeout)
        if len(kutsut) == 1:
            return _VastausTynkka(status_code=429, json_data={"code": 429})
        return _VastausTynkka(json_data={"values": [{"rsi": "50"}]})

    nukutut = []
    monkeypatch.setattr(tekninen_analyytikko.requests, "get", _fake_get)
    monkeypatch.setattr(tekninen_analyytikko.time, "sleep", lambda s: nukutut.append(s))

    data = tekninen_analyytikko._fetch_twelvedata_json("url", {"symbol": "AAPL"})

    assert data == {"values": [{"rsi": "50"}]}
    assert len(kutsut) == 2
    assert tekninen_analyytikko.LIMIT_WAIT_SECONDS in nukutut


def test_hae_json_kayttaa_erillisia_yhteys_ja_lukuaikakatkaisuja(monkeypatch):
    saadut_timeoutit = []

    def _fake_get(url, params=None, timeout=None):
        saadut_timeoutit.append(timeout)
        return _VastausTynkka(json_data={"values": []})

    monkeypatch.setattr(tekninen_analyytikko.requests, "get", _fake_get)
    monkeypatch.setattr(tekninen_analyytikko.time, "sleep", lambda s: None)

    tekninen_analyytikko._fetch_twelvedata_json("url", {"symbol": "AAPL"})

    assert saadut_timeoutit == [
        (tekninen_analyytikko.CONNECT_TIMEOUT, tekninen_analyytikko.READ_TIMEOUT)
    ]


# --- run(): tietokantaan kirjoittava kerros, hakufunktiot monkeypatchattu ---


def test_aja_kirjoittaa_pisteytysrivin_oikealla_ajo_idlla_ja_hinnalla(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data = _price_data(
        current_price=150.0, sma50=140.0, sma200=130.0, high_52w=180.0, low_52w=100.0
    )

    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 2.0)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 55.0)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["symbol"] == "AAPL"
    assert rivit[0]["run_id"] == "ajo1"
    assert rivit[0]["price"] == 150.0
    assert rivit[0]["stop_loss"] == 146.0  # 150 - 2 * 2.0
    assert rivit[0]["target_price"] == 180.0  # 52 viikon huippu
    assert rivit[0]["rr_ratio"] == 7.5  # (180-150) / (150-146)


def test_aja_ohittaa_alle_min_osakekurssin_eika_kutsu_atr_rsi(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="PENNY",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data = _price_data(current_price=2.0)

    def _ei_saa_kutsua(symbol):
        raise AssertionError("ATR/RSI-hakua ei saa tehda alle MIN_SHARE_PRICE-hinnalla")

    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", _ei_saa_kutsua)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", _ei_saa_kutsua)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert rivit == []


def test_aja_ohittaa_alle_min_paivavaihdon_eika_kutsu_atr_rsi(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="MICRO",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data = _price_data(current_price=20.0, avg_daily_turnover_usd=800_000)

    def _ei_saa_kutsua(symbol):
        raise AssertionError("ATR/RSI-hakua ei saa tehda alle MIN_DAILY_TURNOVER-rajan")

    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", _ei_saa_kutsua)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", _ei_saa_kutsua)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert rivit == []


def test_aja_paastaa_lapi_riittavan_paivavaihdon(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="BIGCO",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data = _price_data(
        current_price=100.0,
        sma50=90.0,
        sma200=80.0,
        high_52w=120.0,
        low_52w=60.0,
        avg_daily_turnover_usd=40_000_000,
    )

    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["symbol"] == "BIGCO"


def test_loki_kertoo_kumpi_raja_hylkasi_symbolin(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="PENNY",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="MICRO",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_penny = _price_data(current_price=2.0)
    price_data_micro = _price_data(current_price=20.0, avg_daily_turnover_usd=800_000)

    def _fetch_price_data(symbol):
        return price_data_penny if symbol == "PENNY" else price_data_micro

    viestit = []
    monkeypatch.setattr(
        tekninen_analyytikko._loki, "info", lambda viesti: viestit.append(viesti)
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    # next(): valitaan nimenomaan hylkaysperustelu, ei esim. "viimeisin
    # palkki"-lokiriviä joka mainitsee saman symbolin.
    penny_viesti = next(v for v in viestit if "PENNY" in v and "MIN_SHARE_PRICE" in v)
    micro_viesti = next(
        v for v in viestit if "MICRO" in v and "MIN_DAILY_TURNOVER" in v
    )

    assert "MIN_SHARE_PRICE" in penny_viesti
    assert "MIN_DAILY_TURNOVER" in micro_viesti


def test_aja_yhden_symbolin_epaonnistuminen_ei_esta_muita(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="BAD",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="GOOD",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_hyva = _price_data(
        current_price=100.0, sma50=90.0, sma200=80.0, high_52w=120.0, low_52w=60.0
    )

    def _fetch_price_data(symbol):
        if symbol == "BAD":
            raise requests.exceptions.RequestException("simuloitu verkkovirhe")
        return price_data_hyva

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    summary = tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["symbol"] == "GOOD"
    assert summary == "1 pisteytysta kirjattu, 1 epaonnistui (BAD)"


def test_aja_kaikki_onnistuu_yhteenveto_ilman_epaonnistumisosaa(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="GOOD",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_hyva = _price_data(
        current_price=100.0, sma50=90.0, sma200=80.0, high_52w=120.0, low_52w=60.0
    )

    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data_hyva
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    summary = tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    assert summary == "1 pisteytysta kirjattu"


def test_aja_ohjelmointivirhe_calculate_scoressa_ei_ohitu_hiljaa(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="BUGGY",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_hyva = _price_data(
        current_price=100.0, sma50=90.0, sma200=80.0, high_52w=120.0, low_52w=60.0
    )

    def _rikkinainen_calculate_score(price_data, atr, rsi):
        raise KeyError("ei liity kurssidatan jasentamiseen")

    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data_hyva
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)
    monkeypatch.setattr(
        tekninen_analyytikko, "calculate_score", _rikkinainen_calculate_score
    )

    with pytest.raises(KeyError):
        tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)
