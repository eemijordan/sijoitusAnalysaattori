import sqlite3

import pytest

from agents import paatoksentekija
from core import tietokanta
from core.asetukset import (
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    MAX_BUYS_PER_RUN,
    MAX_POSITIONS,
    POSITION_SIZE,
    RR_THRESHOLD,
)


def _score(
    symbol="AAPL",
    risk_reward_score=8.0,
    price=100.0,
    ema_fast=110.0,
    ema_slow=100.0,
    ema_cross_date=None,
):
    return {
        "symbol": symbol,
        "risk_reward_score": risk_reward_score,
        "price": price,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_cross_date": ema_cross_date,
    }


def _candidate(reference_price=100.0, score=5.0):
    return {"reference_price": reference_price, "score": score}


def _state(
    active_count=0,
    cash=10000.0,
    active_symbols=None,
    buys_this_run=0,
    cooldowns=None,
):
    return {
        "active_count": active_count,
        "cash": cash,
        "active_symbols": active_symbols or set(),
        "buys_this_run": buys_this_run,
        "cooldowns": cooldowns or {},
    }


def _cooldown(until="2026-10-05", sold_on="2026-09-05"):
    return {"until": until, "sold_on": sold_on}


def _position(
    symbol="AAPL",
    stop_loss=None,
    target_price=None,
    buy_date="2026-01-01T00:00:00",
):
    return {
        "symbol": symbol,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "buy_date": buy_date,
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


def test_jaahylla_oleva_symboli_hylataan():
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(_score(), _candidate(), state)

    assert decision == "hylatty"
    assert "jaahylla 2026-10-05 asti" in rationale


def test_symboli_ei_jaahylla_menee_lapi_normaalisti():
    # Kohta 9 (regressio): symboli joka ei ole jaahylla -> kaytos ennallaan,
    # perustelussa ei mainita jaahya lainkaan.
    state = _state(cooldowns={"MSFT": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(
        _score(symbol="AAPL", ema_cross_date="2026-09-20"), _candidate(), state
    )
    decision_ilman, rationale_ilman = paatoksentekija.decide_buy(
        _score(symbol="AAPL", ema_cross_date="2026-09-20"), _candidate(), _state()
    )

    assert decision == "osta"
    assert "jaahy" not in rationale
    assert (decision, rationale) == (decision_ilman, rationale_ilman)


# --- decide_buy(): jaahyn ohitus kun nousutrendi alkaa uudelleen (vaihe 3e) ---
#
# Symboli myyty 2026-09-05 (10 paivaa ennen "tanaan" 2026-09-15), jaahy
# 2026-10-05 asti.


def test_jaahy_ohitetaan_kun_risteys_myynnin_jalkeen():
    score = _score(ema_fast=110.0, ema_slow=100.0, ema_cross_date="2026-09-10")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "osta"
    assert (
        f"jaahy ohitettu: EMA{EMA_FAST_PERIOD} nousi EMA{EMA_SLOW_PERIOD}:n yli "
        f"2026-09-10, myynnin (2026-09-05) jalkeen"
    ) in rationale


def test_jaahy_ohitetaan_kun_risteys_myyntipaivana():
    # Raja sisaltyy: myyntipaivan risteys syntyi myyntia seuraavassa sessiossa.
    score = _score(ema_fast=110.0, ema_slow=100.0, ema_cross_date="2026-09-05")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "osta"
    assert "jaahy ohitettu" in rationale
    assert "2026-09-05" in rationale


def test_jaahy_pysyy_kun_risteys_ennen_myyntia():
    score = _score(ema_fast=110.0, ema_slow=100.0, ema_cross_date="2026-08-20")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert rationale == (
        "jaahylla 2026-10-05 asti; viimeisin risteys 2026-08-20 "
        "ennen myyntia (2026-09-05)"
    )


def test_jaahy_pysyy_kun_ema_nopea_alle():
    score = _score(ema_fast=95.0, ema_slow=100.0, ema_cross_date="2026-08-20")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert rationale == (
        f"jaahylla 2026-10-05 asti; EMA{EMA_FAST_PERIOD} yha "
        f"EMA{EMA_SLOW_PERIOD}:n alapuolella"
    )


def test_jaahy_pysyy_kun_ylos_ja_taas_alas_myynnin_jalkeen():
    # Risteys myynnin jalkeen, mutta se oli alasristeys: nopea on nyt alla.
    score = _score(ema_fast=95.0, ema_slow=100.0, ema_cross_date="2026-09-12")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "alapuolella" in rationale
    assert "ohitettu" not in rationale


@pytest.mark.parametrize(
    ("ema_fast", "ema_slow"),
    [(None, None), (110.0, None), (None, 100.0)],
)
def test_jaahy_pysyy_kun_ema_data_puuttuu(ema_fast, ema_slow):
    score = _score(ema_fast=ema_fast, ema_slow=ema_slow, ema_cross_date="2026-09-10")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert rationale == "jaahylla 2026-10-05 asti; EMA-dataa ei ole"


def test_jaahyn_ohitus_ei_ohita_rr_kynnysta():
    score = _score(
        risk_reward_score=RR_THRESHOLD - 1,
        ema_fast=110.0,
        ema_slow=100.0,
        ema_cross_date="2026-09-10",
    )
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "RR_THRESHOLD" in rationale
    assert "jaahy" not in rationale


def test_jaahyn_ohitus_ei_ohita_kapasiteettiehtoja():
    score = _score(ema_fast=110.0, ema_slow=100.0, ema_cross_date="2026-09-10")
    state = _state(cooldowns={"AAPL": _cooldown()}, buys_this_run=MAX_BUYS_PER_RUN)

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "MAX_BUYS_PER_RUN" in rationale


def test_stop_myynti_ehjassa_trendissa_jaahy_pysyy():
    # Myyty stopilla kun nopea EMA oli yha hitaan ylapuolella: viimeisin
    # (ylos)risteys on kaukana ennen myyntia -> sama veto, jaahy pysyy.
    score = _score(ema_fast=110.0, ema_slow=100.0, ema_cross_date="2026-03-02")
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "viimeisin risteys 2026-03-02 ennen myyntia (2026-09-05)" in rationale


def test_jaahy_pysyy_kun_risteysta_ei_havaittu():
    # Nopea EMA ollut ylapuolella koko historian: ei nayttoa trendin paluusta.
    score = _score(ema_fast=110.0, ema_slow=100.0, ema_cross_date=None)
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert rationale.startswith("jaahylla 2026-10-05 asti; ")
    assert "ohitettu" not in rationale


def test_ostoraja_taynna_hylkaa():
    state = _state(buys_this_run=MAX_BUYS_PER_RUN)

    decision, rationale = paatoksentekija.decide_buy(_score(), _candidate(), state)

    assert decision == "hylatty"
    assert "MAX_BUYS_PER_RUN" in rationale


def test_ostoraja_ei_taynna_paasee_lapi():
    state = _state(buys_this_run=MAX_BUYS_PER_RUN - 1)

    decision, _ = paatoksentekija.decide_buy(_score(), _candidate(), state)

    assert decision == "osta"


def test_jaahy_tarkistetaan_ennen_laatuehtoja():
    # Kerrosjarjestys: jaahy on kelpoisuuskerroksessa, RR-kynnys laatukerroksessa.
    # Ehdokas joka on seka jaahylla etta alittaa RR-kynnyksen saa jaahy-perustelun.
    score = _score(risk_reward_score=RR_THRESHOLD - 1)
    state = _state(cooldowns={"AAPL": _cooldown()})

    decision, rationale = paatoksentekija.decide_buy(score, _candidate(), state)

    assert decision == "hylatty"
    assert "jaahylla" in rationale
    assert "RR_THRESHOLD" not in rationale


def test_kovat_rajat_tarkistetaan_ennen_ostorajaa():
    # Kerrosjarjestys: kateinen on kapasiteettikerroksen kova raja,
    # MAX_BUYS_PER_RUN sen politiikkaehto. Ehdokas jolta loppuu kateinen
    # ja joka samalla ylittaisi ostorajan saa kateis-perustelun.
    state = _state(cash=POSITION_SIZE - 1, buys_this_run=MAX_BUYS_PER_RUN)

    decision, rationale = paatoksentekija.decide_buy(_score(), _candidate(), state)

    assert decision == "hylatty"
    assert "POSITION_SIZE" in rationale
    assert "MAX_BUYS_PER_RUN" not in rationale


# --- decide_hold_or_sell(): puhdas logiikka, ei tietokantaa eika verkkoa ---
#
# Vaihe 3d poisti tavoitehinta- ja pisterajaehdot kokonaan (ks. CLAUDE.md):
# kiinteä tuottotavoite katkaisisi nousun kesken, ja ostohetken laatupisteet
# (risk_reward_score) rankaisevat juuri vahvaa nousua. Vanhat testit korvautuvat
# nain:
# - test_hinta_tavoitteessa_tai_yli_myy, test_stop_puuttuu_tavoite_osuu_myy
#   -> test_regressio_tavoitehinta_ei_enaa_aiheuta_myyntia (kohta 17)
# - test_pisteet_alle_exit_kynnyksen_myy
#   -> test_regressio_matalat_pisteet_eivat_enaa_aiheuta_myyntia (kohta 18)
# - test_pisteet_hystereesikaistalla_pitaa, test_pisteet_yli_rr_kynnyksen_pitaa
#   -> hystereesi RR_THRESHOLD:n ja EXIT_SCORE_THRESHOLD:n valilla ei ole enaa
#      olemassa (EXIT_SCORE_THRESHOLD poistettu), joten kaista-kasite ei kuvaa
#      mitaan tilannetta talla saannolla - ks. sen sijaan
#      test_trendi_jatkuu_ema_fast_yli_pitaa (kohta 13) ja kohdat 17-18.


def test_hinta_stopin_alla_myy():
    position = _position(stop_loss=100.0)
    score = _score(price=95.0)

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "myy"
    assert "stop" in rationale.lower()


def test_score_none_pitaa():
    position = _position()

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, None)

    assert decision == "pida"
    assert "puuttu" in rationale


def test_hinta_puuttuu_pisteytyksesta_pitaa():
    position = _position()
    score = _score(price=None)

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"
    assert "puuttu" in rationale


def test_trendi_paattyi_risteys_oston_jalkeen_myy():
    position = _position(buy_date="2026-01-01T00:00:00")
    score = _score(
        price=100.0, ema_fast=90.0, ema_slow=100.0, ema_cross_date="2026-02-01"
    )

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "myy"
    assert f"EMA{EMA_FAST_PERIOD}" in rationale
    assert f"EMA{EMA_SLOW_PERIOD}" in rationale
    assert "2026-02-01" in rationale


def test_risteys_ostopaivana_myy():
    position = _position(buy_date="2026-01-01T14:17:02")
    score = _score(
        price=100.0, ema_fast=90.0, ema_slow=100.0, ema_cross_date="2026-01-01"
    )

    decision, _ = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "myy"


def test_risteys_ennen_ostoa_pitaa():
    position = _position(buy_date="2026-01-10T00:00:00")
    score = _score(
        price=100.0, ema_fast=90.0, ema_slow=100.0, ema_cross_date="2025-12-01"
    )

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"
    assert "2026-01-10" in rationale


def test_ema_cross_date_none_pitaa():
    # Nopea EMA on alle mutta risteysta ei ole havaittu koko sarjan aikana
    # (find_last_cross palauttaa None) - eli emme tieda oliko se jo alla
    # ennen ostoa. Ei myyda tuntemattoman perusteella.
    position = _position(buy_date="2026-01-01T00:00:00")
    score = _score(price=100.0, ema_fast=90.0, ema_slow=100.0, ema_cross_date=None)

    decision, _ = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"


def test_trendi_jatkuu_ema_fast_yli_pitaa():
    position = _position()
    score = _score(price=100.0, ema_fast=110.0, ema_slow=100.0)

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"
    assert f"EMA{EMA_FAST_PERIOD}" in rationale
    assert f"EMA{EMA_SLOW_PERIOD}" in rationale


def test_epasaannollinen_ajo_risteys_kolme_paivaa_sitten_myy():
    # Todistaa etta saanto kestaa epasaannolliset ajot: risteys ei osunut
    # millekaan ajopaivalle, mutta se on silti ostopaivan jalkeen, joten
    # seuraava tarkistus - riippumatta valissa olleiden paivien maarasta -
    # myy silti.
    position = _position(buy_date="2026-01-01T00:00:00")
    score = _score(
        price=100.0, ema_fast=90.0, ema_slow=100.0, ema_cross_date="2026-03-19"
    )

    decision, _ = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "myy"


def test_ema_data_puuttuu_pitaa():
    position = _position()
    score = _score(price=100.0, ema_fast=None, ema_slow=None)

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"
    assert "vain stop suojaa" in rationale


def test_stop_voittaa_risteytyneen_trendin():
    # Stop tarkistetaan ennen trendiehtoa: jos kurssi on rikkonut stopin,
    # silla ei ole valia missa EMA:t ovat - riskiraja on riskiraja.
    position = _position(stop_loss=100.0, buy_date="2026-01-01T00:00:00")
    score = _score(
        price=95.0, ema_fast=90.0, ema_slow=100.0, ema_cross_date="2026-02-01"
    )

    decision, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "myy"
    assert "stop" in rationale.lower()
    assert "trendi" not in rationale.lower()


def test_regressio_tavoitehinta_ei_enaa_aiheuta_myyntia():
    # target_price tallennetaan ja luetaan silti (52 viikon huippu, tietoa
    # varten) mutta ei enaa vaikuta paatokseen.
    position = _position(target_price=120.0, stop_loss=None)
    score = _score(price=500.0, ema_fast=110.0, ema_slow=100.0)

    decision, _ = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"


def test_regressio_matalat_pisteet_eivat_enaa_aiheuta_myyntia():
    position = _position()
    score = _score(price=100.0, risk_reward_score=3.0, ema_fast=110.0, ema_slow=100.0)

    decision, _ = paatoksentekija.decide_hold_or_sell(position, score)

    assert decision == "pida"


def test_perustelu_kayttaa_vakioiden_jaksoja(monkeypatch):
    monkeypatch.setattr(paatoksentekija, "EMA_SLOW_PERIOD", 50)

    position = _position()
    score = _score(price=100.0, ema_fast=110.0, ema_slow=100.0)

    _, rationale = paatoksentekija.decide_hold_or_sell(position, score)

    assert "EMA20" in rationale
    assert "EMA50" in rationale


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


def test_aja_max_buys_per_run_rajoittaa_ostot(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

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

    assert len(decisions) == 3
    assert len(ostot) == MAX_BUYS_PER_RUN
    assert len(hylatyt) == 1
    assert "MAX_BUYS_PER_RUN" in hylatyt[0]["rationale"]


def test_aja_hylatty_ehdokas_ei_kuluta_ostokiintiota(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    # kaksi liian huonoa (RR alle kynnyksen) ja kaksi kelpaavaa - hylattyjen
    # ei pida kuluttaa MAX_BUYS_PER_RUN-kiintiota, joten molemmat kelpaavat
    # ostetaan.
    for symbol, rr in (
        ("HUONO1", RR_THRESHOLD - 1),
        ("HUONO2", RR_THRESHOLD - 1),
        ("HYVA1", 9.0),
        ("HYVA2", 9.0),
    ):
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

    assert decisions["HYVA1"] == "osta"
    assert decisions["HYVA2"] == "osta"
    assert decisions["HUONO1"] == "hylatty"
    assert decisions["HUONO2"] == "hylatty"


def test_aja_stopattu_symboli_hylataan_jaahyyn_seuraavassa_ajossa(tmp_path):
    # Vaiheen tarkein testi. Kun stop laukeaa, positio suljetaan ja
    # duplikaattilukko lakkaa patemasta - ilman jaahya Agentti 1 voisi loytaa
    # saman symbolin uudelleen, usein tasmalleen samasta analyytikkorivista
    # (se kelpaa 14 vrk), ja botti ostaisi, stoppaisi ja ostaisi uudelleen
    # ilman etta mitaan uutta tietoa on tullut. Jaahy katkaisee taman kierteen.
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        db_path=polku,
    )
    tietokanta.close_position(
        symbol="AAPL", sell_price=90.0, sell_run_id="ajo0", db_path=polku
    )

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=9.0,
        price=95.0,
        db_path=polku,
    )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "hylatty"
    assert "jaahylla" in decisions[0]["rationale"]
    assert tietokanta.has_open_position("AAPL", db_path=polku) is False


def test_aja_tarkistus_ei_valita_jaahysta_eika_ostorajasta(tmp_path):
    # Regressio: jaahy ja MAX_BUYS_PER_RUN koskevat vain ostoja, eivat myynteja.
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        stop_loss=90.0,
        target_price=130.0,
        db_path=polku,
    )

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=110.0,
        db_path=polku,
    )

    tulos = paatoksentekija.run("ajo1", "tarkistus", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "pida"
    assert "1 pidettya" in tulos


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


def test_aja_tarkistus_myy_stopin_rikkoneen_ja_pitaa_toisen(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="BAD",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        stop_loss=95.0,
        target_price=130.0,
        db_path=polku,
    )
    tietokanta.open_position(
        symbol="GOOD",
        buy_price=50.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        stop_loss=45.0,
        target_price=70.0,
        db_path=polku,
    )

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="BAD",
        risk_reward_score=8.0,
        price=90.0,  # alle stopin (95)
        db_path=polku,
    )
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="tarkistus",
        symbol="GOOD",
        risk_reward_score=8.0,
        price=55.0,  # stopin ja tavoitteen valissa, pisteet hyvat
        db_path=polku,
    )

    tulos = paatoksentekija.run("ajo1", "tarkistus", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    assert len(decisions) == 2

    paatokset = {d["symbol"]: d["decision"] for d in decisions}
    assert paatokset["BAD"] == "myy"
    assert paatokset["GOOD"] == "pida"

    assert tietokanta.has_open_position("BAD", db_path=polku) is False
    assert tietokanta.has_open_position("GOOD", db_path=polku) is True
    assert "1 myytya" in tulos


def test_aja_tarkistus_nolla_positiota_palauttaa_siististi(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tulos = paatoksentekija.run("ajo_tyhja", "tarkistus", db_path=polku)

    assert "0 aktiivista positiota" in tulos
    assert tietokanta.get_decisions("ajo_tyhja", db_path=polku) == []


def test_aja_tarkistus_positio_ilman_pisteytysta_pidetaan(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo0",
        stop_loss=90.0,
        target_price=130.0,
        db_path=polku,
    )
    # ei pisteytysta talle ajolle (esim. vaiheen 2f verkkovirhe)

    tulos = paatoksentekija.run("ajo1", "tarkistus", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "pida"
    assert tietokanta.has_open_position("AAPL", db_path=polku) is True
    assert "1 pidettya" in tulos


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


# --- vaihe 2g: stop_loss/target_price kulkevat pisteytykselta positioon ---


def test_aja_positio_saa_pisteytyksen_stopin_ja_tavoitehinnan(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=100.0,
        stop_loss=90.0,
        target_price=130.0,
        rr_ratio=3.0,
        db_path=polku,
    )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    positiot = tietokanta.get_open_positions(db_path=polku)
    assert len(positiot) == 1
    assert positiot[0]["stop_loss"] == 90.0
    assert positiot[0]["target_price"] == 130.0


def test_aja_positio_avataan_vaikka_stop_ja_tavoite_puuttuvat(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    # stop_loss/target_price jaavat Noneksi esim. jos osake on 52 viikon
    # huipussaan - positio pitaa silti avata (3b osaa myohemmin sulkea
    # pelkan stopin perusteella, puuttuva tavoite tarkoittaa vain ettei
    # automaattista voitonottoa ole).
    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=8.0,
        price=100.0,
        db_path=polku,
    )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    positiot = tietokanta.get_open_positions(db_path=polku)
    assert len(positiot) == 1
    assert positiot[0]["stop_loss"] is None
    assert positiot[0]["target_price"] is None


def test_regressio_huono_rr_ratio_ei_esta_ostoa(tmp_path):
    # TARKEIN TESTI: rr_ratio on mittari, ei saanto (ks. CLAUDE.md). Se ei saa
    # vaikuttaa ostopaatokseen riippumatta siita kuinka huono se on - vain
    # risk_reward_score (laatupisteytys) ja RR_THRESHOLD ratkaisevat.
    # rr_ratio otetaan kayttoon paatoksenteossa vasta jos suljetut positiot
    # myohemmin osoittavat sen erottelevan - jos tama testi alkaa epaonnistua,
    # se on merkki etta joku on lisannyt rr_ratio-kynnyksen decide_buy():hyn
    # ilman sita mittausta, ei etta testi pitaisi korjata.
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.insert_score(
        run_id="ajo1",
        run_type="normaali",
        symbol="AAPL",
        risk_reward_score=RR_THRESHOLD,
        price=100.0,
        stop_loss=90.0,
        target_price=102.0,
        rr_ratio=0.2,
        db_path=polku,
    )

    paatoksentekija.run("ajo1", "normaali", db_path=polku)

    decisions = tietokanta.get_decisions("ajo1", db_path=polku)
    assert decisions[0]["decision"] == "osta"


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


# --- tietokanta.close_position() ---


def test_sulje_positio_voitollinen_kasvattaa_kateista_myyntitulolla(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )
    kateinen_oston_jalkeen = tietokanta.get_cash(db_path=polku)

    suljettu = tietokanta.close_position(
        symbol="AAPL", sell_price=120.0, sell_run_id="ajo2", db_path=polku
    )

    assert suljettu["pnl_usd"] == 200.0
    assert suljettu["pnl_pct"] == 20.0
    # kateinen kasvaa myyntitulolla (quantity * sell_price = 10 * 120 = 1200),
    # ei alkuperaisella panoksella (1000) - tama on koko funktion helpoin
    # kohta mennä pieleen.
    assert tietokanta.get_cash(db_path=polku) == kateinen_oston_jalkeen + 1200.0


def test_sulje_positio_tappiollinen_kasvattaa_kateista_myyntitulolla(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )
    kateinen_oston_jalkeen = tietokanta.get_cash(db_path=polku)

    suljettu = tietokanta.close_position(
        symbol="AAPL", sell_price=80.0, sell_run_id="ajo2", db_path=polku
    )

    assert suljettu["pnl_usd"] == -200.0
    assert suljettu["pnl_pct"] == -20.0
    assert tietokanta.get_cash(db_path=polku) == kateinen_oston_jalkeen + 800.0


def test_sulje_positio_taydentaa_kentat(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )

    tietokanta.close_position(
        symbol="AAPL", sell_price=110.0, sell_run_id="ajo2", db_path=polku
    )

    yhteys = tietokanta.open_connection(polku)
    try:
        rivi = yhteys.execute(
            "SELECT * FROM positions WHERE symbol = 'AAPL'"
        ).fetchone()
    finally:
        yhteys.close()

    assert rivi["status"] == "suljettu"
    assert rivi["sell_price"] == 110.0
    assert rivi["sell_date"] is not None
    assert rivi["sell_run_id"] == "ajo2"
    assert rivi["last_checked"] is not None


def test_sulje_positio_ilman_aktiivista_positiota_nostaa_valueerrorin(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    with pytest.raises(ValueError):
        tietokanta.close_position(
            symbol="AAPL", sell_price=100.0, sell_run_id="ajo1", db_path=polku
        )


def test_sulje_positio_ei_positiivinen_myyntihinta_nostaa_valueerrorin(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )
    kateinen_ennen = tietokanta.get_cash(db_path=polku)

    with pytest.raises(ValueError):
        tietokanta.close_position(
            symbol="AAPL", sell_price=0.0, sell_run_id="ajo2", db_path=polku
        )

    assert tietokanta.get_cash(db_path=polku) == kateinen_ennen
    assert tietokanta.has_open_position("AAPL", db_path=polku) is True


def test_sulkemisen_jalkeen_symboli_voidaan_ostaa_uudelleen(tmp_path):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    tietokanta.open_position(
        symbol="AAPL",
        buy_price=100.0,
        stake_usd=1000.0,
        buy_run_id="ajo1",
        db_path=polku,
    )
    tietokanta.close_position(
        symbol="AAPL", sell_price=110.0, sell_run_id="ajo2", db_path=polku
    )

    uusi_id = tietokanta.open_position(
        symbol="AAPL",
        buy_price=90.0,
        stake_usd=1000.0,
        buy_run_id="ajo3",
        db_path=polku,
    )

    assert uusi_id > 0
    assert tietokanta.has_open_position("AAPL", db_path=polku) is True
