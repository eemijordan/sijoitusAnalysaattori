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


# --- resample_weekly(): puhdas logiikka, ei verkkoa eika tietokantaa ---


def test_resample_viikon_ylin_ja_alin_ovat_paivien_aariarvot():
    paivat = [
        _paivapalkki("2026-01-05", high=105.0, low=99.0),
        _paivapalkki("2026-01-06", high=110.0, low=95.0),
        _paivapalkki("2026-01-07", high=102.0, low=100.0),
    ]

    viikot = tekninen_analyytikko.resample_weekly(paivat)

    assert len(viikot) == 1
    assert viikot[0]["high"] == 110.0
    assert viikot[0]["low"] == 95.0
    assert viikot[0]["date"] == "2026-01-05"


def test_resample_vuodenvaihteen_yli_menva_viikko_on_yksi_viikko():
    # 2026-01-01 on torstai, joten ISO-viikko 1/2026 alkaa ma 2025-12-29 ja
    # sisaltaa myos joulukuun paivat - vuodenvaihde ei saa katkaista viikkoa.
    paivat = [
        _paivapalkki("2025-12-30", high=100.0, low=90.0),
        _paivapalkki("2026-01-01", high=105.0, low=95.0),
    ]

    viikot = tekninen_analyytikko.resample_weekly(paivat)

    assert len(viikot) == 1
    assert viikot[0]["high"] == 105.0
    assert viikot[0]["low"] == 90.0


def test_resample_uusin_ensin_syote_palauttaa_vanhimmasta_uusimpaan():
    paivat = [
        _paivapalkki("2026-01-13", high=120.0, low=118.0),
        _paivapalkki("2026-01-12", high=119.0, low=117.0),
        _paivapalkki("2026-01-06", high=110.0, low=95.0),
        _paivapalkki("2026-01-05", high=105.0, low=99.0),
    ]

    viikot = tekninen_analyytikko.resample_weekly(paivat)

    assert len(viikot) == 2
    assert viikot[0]["date"] == "2026-01-05"
    assert viikot[0]["high"] == 110.0
    assert viikot[1]["date"] == "2026-01-12"
    assert viikot[1]["high"] == 120.0


# --- find_swing_lows(): ZigZag, raja 10 %, ei aivan tarkalla rajalla ---


def _bar(low, high=None):
    return {"date": "x", "high": high if high is not None else low, "low": low}


def test_swing_kunnon_heilahdus_loytyy():
    bars = [_bar(100), _bar(85), _bar(100)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert [p["low"] for p in pohjat] == [85]


def test_swing_pieni_notkahdus_ei_kelpaa():
    bars = [_bar(100), _bar(95), _bar(100)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert pohjat == []


def test_swing_notkahdus_ison_heilahduksen_sisalla_ohitetaan():
    bars = [_bar(100), _bar(80), _bar(90), _bar(84), _bar(100)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert [p["low"] for p in pohjat] == [80]


def test_swing_sama_palkki_ei_vahvista_itseaan():
    # b1=88 kaantaa suunnan laskuun. b2 tekee uuden pohjan 75 (oma high 88,
    # eli oman pohjansa suhteen 17 % - siita huolimatta se ei vahvista
    # itseaan, koska vahvistus katsoo vanhaa aariarvoa (88), ei omaa.
    # Vasta b3 (high 90, +20 % pohjasta 75) vahvistaa pohjan.
    bars = [
        _bar(low=100, high=100),
        _bar(low=88, high=88),
        _bar(low=75, high=88),
        _bar(low=85, high=90),
    ]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert [p["low"] for p in pohjat] == [75]


def test_swing_palkki_joka_alittaa_pohjan_ei_vahvista_vanhaa_pohjaa():
    # 100 -> 80 kaantaa suunnan laskuun (pohja 80). Kolmas palkki painaa
    # pohjan syvemmalle (75) JA nousee samalla palkilla yli 10 % vanhasta
    # pohjasta (80): 90 >= 80*1.10=88. Jos vahvistus tarkistettaisiin ennen
    # uutta pohjaa, tama vahvistaisi virheellisesti vanhan pohjan 80, vaikka
    # kurssi kavi juuri sen alapuolella (75). Oikea pohja on 75, ja se
    # vahvistuu vasta neljannella palkilla (95 >= 75*1.10=82.5).
    bars = [_bar(100), _bar(80), _bar(low=75, high=90), _bar(95)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert [p["low"] for p in pohjat] == [75]


def test_swing_vahvistamaton_viimeinen_pohja_ei_kelpaa():
    bars = [_bar(100), _bar(80)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert pohjat == []


def test_swing_useita_heilahduksia_palautetaan_vanhimmasta_uusimpaan():
    bars = [_bar(100), _bar(80), _bar(100), _bar(70), _bar(100)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert [p["low"] for p in pohjat] == [80, 70]


def test_swing_alkutila_ylospain_kaantyva_sarja_ei_tuota_pohjaa():
    bars = [_bar(100), _bar(97), _bar(110)]

    pohjat = tekninen_analyytikko.find_swing_lows(bars, min_move=0.10)

    assert pohjat == []


def test_swing_nouseva_sarja_ja_liian_lyhyt_sarja_palauttavat_tyhjan():
    nouseva = [_bar(100), _bar(105), _bar(110), _bar(120)]
    liian_lyhyt = [_bar(100)]

    assert tekninen_analyytikko.find_swing_lows(nouseva, min_move=0.10) == []
    assert tekninen_analyytikko.find_swing_lows([], min_move=0.10) == []
    assert tekninen_analyytikko.find_swing_lows(liian_lyhyt, min_move=0.10) == []


# --- _select_swing_low_below_price(): pohjan valinta nykyhinnan perusteella ---


def test_uusin_pohja_nykyhinnan_ylapuolella_ohitetaan_valitaan_edellinen():
    pohjat = [_bar(low=80), _bar(low=95)]

    valittu = tekninen_analyytikko._select_swing_low_below_price(
        pohjat, current_price=90.0
    )

    assert valittu == 80


# --- calculate_stop_and_target(): puhdas logiikka, ei verkkoa eika tietokantaa ---
#
# Vaihe 2j korvasi ATR-pohjaisen stopin edellisen pohjan alle asetetulla
# stopilla (ks. CLAUDE.md). Vanhat ATR-testit (puuttuva ATR, valtava ATR joka
# vei stopin negatiiviseksi) eivat enaa kuvaa mitaan tilannetta - stop on nyt
# aina maaritelty prosenttietaisyytena pohjasta tai kattona, koskaan ATR:sta,
# eika voi koskaan menna nollaan tai alle (ks.
# test_stop_on_aina_alle_hinnan_ja_yli_nollan).


def test_stop_viikkopohjasta_kun_se_on_rajan_sisalla():
    stop, lahde, tavoite, rr = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=90.0, swing_low_daily=None, high_52w=130.0
    )

    assert stop == 90.0
    assert lahde == "viikkopohja"
    assert tavoite == 130.0
    assert rr == 3.0  # (130-100) / (100-90)


def test_stop_paivapohjasta_kun_viikkopohja_liian_kaukana():
    stop, lahde, _, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=75.0, swing_low_daily=92.0, high_52w=None
    )

    assert stop == 92.0
    assert lahde == "paivapohja"


def test_stop_katosta_kun_molemmat_pohjat_liian_kaukana():
    stop, lahde, _, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=70.0, swing_low_daily=75.0, high_52w=None
    )

    assert stop == 80.0
    assert lahde == "katto"


def test_stop_paivapohjasta_kun_viikkopohja_puuttuu():
    stop, lahde, _, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=None, swing_low_daily=92.0, high_52w=None
    )

    assert stop == 92.0
    assert lahde == "paivapohja"


def test_stop_katosta_kun_molemmat_pohjat_puuttuvat():
    stop, lahde, _, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=None, swing_low_daily=None, high_52w=None
    )

    assert stop == 80.0
    assert lahde == "katto"


def test_stop_rajatapaus_tasmalleen_20_prosenttia_kelpaa():
    stop, lahde, _, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=80.0, swing_low_daily=None, high_52w=None
    )

    assert stop == 80.0
    assert lahde == "viikkopohja"


def test_huippu_hinnan_alla_tai_yhtasuuri_jattaa_tavoitteen_ja_rr_n_laskematta():
    _, _, tavoite_alle, rr_alle = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=90.0, swing_low_daily=None, high_52w=90.0
    )
    _, _, tavoite_yhtasuuri, rr_yhtasuuri = (
        tekninen_analyytikko.calculate_stop_and_target(
            price=100.0, swing_low_weekly=90.0, swing_low_daily=None, high_52w=100.0
        )
    )

    assert tavoite_alle is None
    assert rr_alle is None
    assert tavoite_yhtasuuri is None
    assert rr_yhtasuuri is None


def test_puuttuva_huippu_jattaa_tavoitteen_laskematta():
    _, _, tavoite, _ = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=90.0, swing_low_daily=None, high_52w=None
    )

    assert tavoite is None


def test_stop_on_aina_alle_hinnan_ja_yli_nollan():
    tapaukset = [
        (100.0, 90.0, None),
        (100.0, None, 92.0),
        (100.0, None, None),
        (5.0, None, None),
    ]
    for price, viikko, paiva in tapaukset:
        stop, _, _, _ = tekninen_analyytikko.calculate_stop_and_target(
            price=price, swing_low_weekly=viikko, swing_low_daily=paiva, high_52w=None
        )
        assert 0 < stop < price


def test_eri_viikkopohja_antaa_eri_rr_suhteen_samalla_hinnalla_ja_huipulla():
    # Todistaa ettei rr_ratio ole vakio: jos stop ja tavoite tulisivat
    # molemmat kiinteista kertoimista, suhde olisi aina sama luku.
    _, _, _, rr_matala_riski = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=95.0, swing_low_daily=None, high_52w=130.0
    )
    _, _, _, rr_korkea_riski = tekninen_analyytikko.calculate_stop_and_target(
        price=100.0, swing_low_weekly=85.0, swing_low_daily=None, high_52w=130.0
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
        current_price=150.0,
        sma50=140.0,
        sma200=130.0,
        high_52w=180.0,
        low_52w=100.0,
        swing_low_weekly=140.0,
        ema_fast=155.0,
        ema_slow=145.0,
        ema_cross_date="2026-06-01",
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
    assert rivit[0]["stop_loss"] == 140.0  # viikkopohja, 6.7 % alle hinnan
    assert rivit[0]["stop_source"] == "viikkopohja"
    assert rivit[0]["target_price"] == 180.0  # 52 viikon huippu
    assert rivit[0]["rr_ratio"] == 3.0  # (180-150) / (150-140)
    assert rivit[0]["ema_fast"] == 155.0
    assert rivit[0]["ema_slow"] == 145.0
    assert rivit[0]["ema_cross_date"] == "2026-06-01"


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


def test_aja_tarkistus_hakee_ja_tallentaa_vertailukurssin(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        db_path=polku,
    )
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="AAPL",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_aapl = _price_data(current_price=100.0)
    price_data_spy = _price_data(current_price=500.0)

    def _fetch_price_data(symbol):
        return price_data_spy if symbol == "SPY" else price_data_aapl

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    tekninen_analyytikko.run("ajo1", "tarkistus", db_path=polku)

    vertailu = tietokanta.get_market_reference("ajo1", "SPY", db_path=polku)
    assert vertailu is not None
    assert vertailu["price"] == 500.0


def test_aja_tarkistus_ilman_avoimia_positioita_tallentaa_vertailukurssin(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    # ei avoimia positioita eika ehdokkaita -> silmukka ei pyori kertaakaan,
    # mutta vertailukurssi pitaa silti tallentua etta tuottokayraan ei jaa
    # aukkoa niina paivina kun salkku on pelkkaa kateista.

    price_data_spy = _price_data(current_price=500.0)
    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data_spy
    )

    tekninen_analyytikko.run("ajo1", "tarkistus", db_path=polku)

    vertailu = tietokanta.get_market_reference("ajo1", "SPY", db_path=polku)
    assert vertailu is not None
    assert vertailu["price"] == 500.0


def test_aja_tarkistus_vertailuhaku_epaonnistuu_ei_kaada_ajoa(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    def _epaonnistu(symbol):
        raise requests.exceptions.RequestException("simuloitu verkkovirhe")

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _epaonnistu)

    summary = tekninen_analyytikko.run("ajo1", "tarkistus", db_path=polku)

    assert tietokanta.get_market_reference("ajo1", "SPY", db_path=polku) is None
    assert summary == "0 pisteytysta kirjattu"


def test_aja_normaali_ei_hae_vertailukurssia(tmp_path, monkeypatch):
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

    price_data_aapl = _price_data(current_price=100.0)

    def _ei_saa_kutsua_spy(symbol):
        if symbol == "SPY":
            raise AssertionError("normaaliajo ei saa hakea vertailukurssia")
        return price_data_aapl

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _ei_saa_kutsua_spy)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    assert tietokanta.get_market_reference("ajo1", "SPY", db_path=polku) is None


def test_aja_kirjaa_ohituksen_kun_hintadata_puuttuu_ja_jatkaa_seuraavaan(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="CVLT",
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
        return None if symbol == "CVLT" else price_data_hyva

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    ohitukset = tietokanta.get_skips("ajo1", db_path=polku)
    assert len(ohitukset) == 1
    assert ohitukset[0]["symbol"] == "CVLT"
    assert ohitukset[0]["stage"] == "hintadata_puuttuu"

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["symbol"] == "GOOD"


def test_aja_kirjaa_ohituksen_hinta_alarajalla_syyssa_hinta_ja_raja(
    tmp_path, monkeypatch
):
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
    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    ohitukset = tietokanta.get_skips("ajo1", db_path=polku)
    assert len(ohitukset) == 1
    assert ohitukset[0]["symbol"] == "PENNY"
    assert ohitukset[0]["stage"] == "hinta_alaraja"
    assert "2.0" in ohitukset[0]["reason"]
    assert str(tekninen_analyytikko.MIN_SHARE_PRICE) in ohitukset[0]["reason"]


def test_aja_kirjaa_ohituksen_vaihto_alarajalla(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    ohitukset = tietokanta.get_skips("ajo1", db_path=polku)
    assert len(ohitukset) == 1
    assert ohitukset[0]["symbol"] == "MICRO"
    assert ohitukset[0]["stage"] == "vaihto_alaraja"


def test_aja_kirjaa_ohituksen_kun_haku_epaonnistuu(tmp_path, monkeypatch):
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

    def _fetch_price_data(symbol):
        raise requests.exceptions.RequestException("simuloitu verkkovirhe")

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    ohitukset = tietokanta.get_skips("ajo1", db_path=polku)
    assert len(ohitukset) == 1
    assert ohitukset[0]["symbol"] == "BAD"
    assert ohitukset[0]["stage"] == "haku_epaonnistui"
    assert "simuloitu verkkovirhe" in ohitukset[0]["reason"]


def test_ohitettu_symboli_ei_saa_pisteytysta(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert [r["symbol"] for r in rivit] == []


def test_onnistunut_symboli_ei_tuota_ohitusrivia(tmp_path, monkeypatch):
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

    tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    assert tietokanta.get_skips("ajo1", db_path=polku) == []


def test_yksi_ohitus_ei_kaada_muun_ajon_pisteytysta(tmp_path, monkeypatch):
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
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="normaali",
        symbol="GOOD",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_micro = _price_data(current_price=20.0, avg_daily_turnover_usd=800_000)
    price_data_hyva = _price_data(
        current_price=100.0, sma50=90.0, sma200=80.0, high_52w=120.0, low_52w=60.0
    )

    def _fetch_price_data(symbol):
        return price_data_micro if symbol == "MICRO" else price_data_hyva

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    summary = tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_scores("ajo1", db_path=polku)
    assert [r["symbol"] for r in rivit] == ["GOOD"]
    assert "1 ohitettu" in summary


def test_incomplete_price_data_error_ei_kirjaa_ohitusta(tmp_path, monkeypatch):
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

    def _fetch_price_data(symbol):
        raise tekninen_analyytikko.IncompletePriceDataError("kesken oleva palkki")

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)

    with pytest.raises(tekninen_analyytikko.IncompletePriceDataError):
        tekninen_analyytikko.run("ajo1", "normaali", db_path=polku)

    assert tietokanta.get_skips("ajo1", db_path=polku) == []


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


# --- vaihe 2j: olemassa oleva position oma stop ei muutu tarkistusajossa ---


def test_tarkistusajo_ei_muuta_position_omaa_ostohetken_stoppia(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        stop_loss=85.0,  # mallin 1.1 (ATR-pohjainen) stop ostohetkella
        db_path=polku,
    )
    tietokanta.insert_candidate(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="AAPL",
        source="analyytikko",
        advanced=1,
        db_path=polku,
    )

    price_data_aapl = _price_data(current_price=100.0, swing_low_weekly=88.0)
    price_data_spy = _price_data(current_price=500.0)

    def _fetch_price_data(symbol):
        return price_data_spy if symbol == "SPY" else price_data_aapl

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _fetch_price_data)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 1.5)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 50.0)

    tekninen_analyytikko.run("ajo1", "tarkistus", db_path=polku)

    positio = tietokanta.get_open_positions(db_path=polku)[0]
    assert positio["stop_loss"] == 85.0  # ostohetken stop, uusi saanto ei koske sita

    uusi_pisteytys = tietokanta.get_scores("ajo1", db_path=polku)[0]
    assert uusi_pisteytys["symbol"] == "AAPL"
    assert uusi_pisteytys["stop_loss"] == 88.0  # eri kuin position oma stop
    assert uusi_pisteytys["stop_source"] == "viikkopohja"


# --- calculate_ema(): puhdas logiikka, ei verkkoa eika tietokantaa (vaihe 3d) ---


def test_ema_vakiosarja_antaa_saman_vakion():
    sarja = [50.0] * 10

    ema = tekninen_analyytikko.calculate_ema(sarja, period=3)

    assert ema[2:] == [50.0] * 8


def test_ema_kasin_laskettava_sarja():
    # a = 2/(3+1) = 0.5. Alustus (1+2+3)/3 = 2. Sitten 0.5*4+0.5*2=3 ja
    # 0.5*5+0.5*3=4.
    sarja = [1.0, 2.0, 3.0, 4.0, 5.0]

    ema = tekninen_analyytikko.calculate_ema(sarja, period=3)

    assert ema == [None, None, 2.0, 3.0, 4.0]


def test_ema_liian_lyhyt_sarja_palauttaa_pelkkia_noneja():
    sarja = [1.0, 2.0]

    ema = tekninen_analyytikko.calculate_ema(sarja, period=3)

    assert ema == [None, None]


# --- find_last_cross(): puhdas logiikka, ei verkkoa eika tietokantaa ---


def test_cross_loytyy_ja_paivamaara_on_oikea():
    paivat = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    fast = [5.0, 5.0, 3.0, 3.0]
    slow = [4.0, 4.0, 4.0, 4.0]

    risteys = tekninen_analyytikko.find_last_cross(fast, slow, paivat)

    assert risteys == "2026-01-03"


def test_cross_ensin_ylos_sitten_alas_palauttaa_viimeisimman():
    paivat = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    fast = [3.0, 5.0, 5.0, 3.0]
    slow = [4.0, 4.0, 4.0, 4.0]

    risteys = tekninen_analyytikko.find_last_cross(fast, slow, paivat)

    assert risteys == "2026-01-04"  # viimeisin on alaspain


def test_cross_ei_risteysta_palauttaa_nonen():
    paivat = ["2026-01-01", "2026-01-02", "2026-01-03"]
    fast = [5.0, 5.0, 5.0]
    slow = [4.0, 4.0, 4.0]

    risteys = tekninen_analyytikko.find_last_cross(fast, slow, paivat)

    assert risteys is None


def test_cross_alun_none_arvot_ohitetaan():
    paivat = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    fast = [None, None, 5.0, 3.0]
    slow = [None, 4.0, 4.0, 4.0]

    risteys = tekninen_analyytikko.find_last_cross(fast, slow, paivat)

    assert risteys == "2026-01-04"


def test_cross_tasatilanne_ei_ole_alapuolella():
    paivat = ["2026-01-01", "2026-01-02", "2026-01-03"]
    fast = [5.0, 4.0, 3.0]
    slow = [4.0, 4.0, 4.0]

    risteys = tekninen_analyytikko.find_last_cross(fast, slow, paivat)

    assert risteys == "2026-01-03"  # ei toisena paivana, koska 4 == 4 ei ole alle


# --- EMA-historia (1000 pv) ei vaikuta muihin laskelmiin (vaihe 3d) ---


def _synteettiset_paivapalkit(maara, viimeisin_paiva):
    """Luo Twelve Data -tyyliset palkit uusin ensin, hinta kasvaa iän mukana.

    Palkki 0 (uusin) on aina hinnaltaan 100 - vain vanhemmat palkit
    (indeksi > 251) ovat kalliimpia, joten 252 uusimman palkin huippu on
    todistettavasti eri kuin koko 1000 palkin huippu.
    """
    from datetime import timedelta

    palkit = []
    for i in range(maara):
        paiva = viimeisin_paiva - timedelta(days=i)
        hinta = 100.0 + i * 0.5
        palkit.append(
            _paivapalkki(paiva.isoformat(), close=hinta, high=hinta + 1, low=hinta - 1)
        )
    return palkit


def test_ema_haku_ei_muuta_muita_laskelmia_1000_vs_252(monkeypatch):
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
    palkit = _synteettiset_paivapalkit(1000, viimeisin_paiva=eilen)

    monkeypatch.setattr(tekninen_analyytikko, "datetime", _KiinteaKello(kiintea_nyt))
    monkeypatch.setattr(
        tekninen_analyytikko, "get_twelve_data_key", lambda: "testiavain"
    )
    monkeypatch.setattr(
        tekninen_analyytikko,
        "_fetch_twelvedata_json",
        lambda url, params: _time_series_vastaus(palkit),
    )

    price_data = tekninen_analyytikko.fetch_price_data("AAPL")
    suoraan_252 = tekninen_analyytikko._calculate_price_stats(
        palkit[: tekninen_analyytikko.PRICE_HISTORY_DAYS]
    )

    assert price_data["high_52w"] == suoraan_252["high_52w"]
    assert price_data["sma50"] == suoraan_252["sma50"]
    assert price_data["sma200"] == suoraan_252["sma200"]
    assert price_data["swing_low_weekly"] == suoraan_252["swing_low_weekly"]
    assert price_data["swing_low_daily"] == suoraan_252["swing_low_daily"]
    assert price_data["history_days"] == tekninen_analyytikko.PRICE_HISTORY_DAYS

    # Todistaa etta 1000 palkkia oikeasti haettiin eika vain 252: koko
    # sarjan huippu on paljon korkeampi kuin 252 uusimman palkin huippu.
    kaikkien_huippu = max(float(p["high"]) for p in palkit)
    assert kaikkien_huippu > price_data["high_52w"]
