import json
from datetime import date, datetime, timedelta, timezone

import pytest

from agents import tekninen_analyytikko, trendinmetsastaja
from core import orkestroija, tietokanta


def test_aja_normaali_paasta_paahan(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"

    # Data-valmiustarkistus (vaihe 2h) korvataan aina valmiiksi tassa
    # tiedostossa - testit eivat saa riippua siita mika kellonaika on New
    # Yorkissa kun ne sattuvat ajettavaksi.
    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))

    tuore_iso = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    tuore_pvm = (date.today() - timedelta(days=5)).isoformat()

    monkeypatch.setattr(
        trendinmetsastaja,
        "fetch_analyst_changes",
        lambda run_id=None: [
            {
                "symbol": "AAPL",
                "publishedDate": tuore_iso,
                "newGrade": "Buy",
                "previousGrade": "Hold",
                "gradingCompany": "Goldman Sachs",
                "action": "upgrade",
            },
        ],
    )
    monkeypatch.setattr(
        trendinmetsastaja,
        "fetch_congress_trades",
        lambda run_id=None: [
            {
                "symbol": "MSFT",
                "transactionDate": tuore_pvm,
                "type": "Purchase",
                "amount": "$100,001 - $250,000",
            },
        ],
    )
    monkeypatch.setattr(
        trendinmetsastaja, "fetch_insider_trades", lambda run_id=None: []
    )
    monkeypatch.setattr(
        trendinmetsastaja, "fetch_price_targets", lambda run_id=None: []
    )
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: {"AAPL", "MSFT"})

    price_data = {
        "current_price": 150.0,
        "sma50": 140.0,
        "sma200": 130.0,
        "high_52w": 180.0,
        "low_52w": 100.0,
        "history_days": 252,
        "avg_turnover": 1_000_000,
    }
    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 2.0)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 55.0)

    ajo_id = orkestroija.run_normal(polku)

    ehdokkaat = tietokanta.get_candidates(ajo_id, advanced_only=False, db_path=polku)
    pisteytykset = tietokanta.get_scores(ajo_id, db_path=polku)
    paatokset = tietokanta.get_decisions(ajo_id, db_path=polku)

    assert len(ehdokkaat) > 0
    assert len(pisteytykset) > 0
    assert len(paatokset) > 0

    for rivi in ehdokkaat + pisteytykset + paatokset:
        assert rivi["run_id"] == ajo_id
        assert rivi["run_type"] == "normaali"

    ajon_rivi = tietokanta.get_run(ajo_id, db_path=polku)
    assert ajon_rivi["status"] == "valmis"
    assert ajon_rivi["finished_at"] is not None
    assert ajon_rivi["model_version"]
    assert "RR_THRESHOLD" in json.loads(ajon_rivi["settings_json"])


# --- koko ketju: myydyn symbolin seuranta ja jaahyn ohitus (vaihe 3e) ---


def _valmistele_seurantaketju(tmp_path, monkeypatch, ema_fast, ema_cross_date):
    """Symboli WMB myyty 10 pv sitten; FMP ei palauta mitaan."""
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    myyty = (date.today() - timedelta(days=10)).isoformat()
    yhteys = tietokanta.open_connection(polku)
    try:
        yhteys.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, buy_date,
                buy_run_id, sell_date
            ) VALUES ('WMB', 'suljettu', 150, 6, 900, '2026-01-01', 'ajo0', ?)
            """,
            (myyty,),
        )
        yhteys.commit()
    finally:
        yhteys.close()

    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))
    for nimi in (
        "fetch_analyst_changes",
        "fetch_congress_trades",
        "fetch_insider_trades",
        "fetch_price_targets",
    ):
        monkeypatch.setattr(trendinmetsastaja, nimi, lambda run_id=None: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: set())

    # ATR 1,3 % (3) + trendi (4) + asema 62,5 % (2) + RSI 55 (1) = 10 pistetta
    price_data = {
        "current_price": 150.0,
        "sma50": 140.0,
        "sma200": 130.0,
        "high_52w": 180.0,
        "low_52w": 100.0,
        "history_days": 252,
        "ema_fast": ema_fast,
        "ema_slow": 140.0,
        "ema_cross_date": ema_cross_date,
    }
    monkeypatch.setattr(
        tekninen_analyytikko, "fetch_price_data", lambda symbol: price_data
    )
    monkeypatch.setattr(tekninen_analyytikko, "fetch_atr", lambda symbol: 2.0)
    monkeypatch.setattr(tekninen_analyytikko, "fetch_rsi", lambda symbol: 55.0)
    return polku, myyty


def test_ketju_seurattava_ema_palannut_ostetaan_takaisin(tmp_path, monkeypatch):
    risteys = (date.today() - timedelta(days=3)).isoformat()
    polku, myyty = _valmistele_seurantaketju(
        tmp_path, monkeypatch, ema_fast=145.0, ema_cross_date=risteys
    )

    ajo_id = orkestroija.run_normal(polku)

    ehdokkaat = tietokanta.get_candidates(ajo_id, advanced_only=True, db_path=polku)
    assert [(r["symbol"], r["source"]) for r in ehdokkaat] == [("WMB", "seuranta")]
    pisteytykset = tietokanta.get_scores(ajo_id, db_path=polku)
    assert pisteytykset[0]["risk_reward_score"] >= 7
    paatokset = tietokanta.get_decisions(ajo_id, db_path=polku)
    assert len(paatokset) == 1
    assert paatokset[0]["decision"] == "osta"
    assert (
        f"jaahy ohitettu: EMA20 nousi EMA100:n yli {risteys}"
        in (paatokset[0]["rationale"])
    )
    assert f"myynnin ({myyty}) jalkeen" in paatokset[0]["rationale"]
    assert tietokanta.has_open_position("WMB", db_path=polku)


def test_ketju_seurattava_ema_yha_alla_hylataan_jaahylla(tmp_path, monkeypatch):
    risteys = (date.today() - timedelta(days=12)).isoformat()
    polku, _ = _valmistele_seurantaketju(
        tmp_path, monkeypatch, ema_fast=135.0, ema_cross_date=risteys
    )

    ajo_id = orkestroija.run_normal(polku)

    paatokset = tietokanta.get_decisions(ajo_id, db_path=polku)
    assert len(paatokset) == 1
    assert paatokset[0]["decision"] == "hylatty"
    assert paatokset[0]["rationale"].startswith("jaahylla ")
    assert "EMA20 yha EMA100:n alapuolella" in paatokset[0]["rationale"]
    assert not tietokanta.has_open_position("WMB", db_path=polku)


def test_kaatuva_agentti_jattaa_ajon_keskeytyi_tilaan_ja_virhetekstin(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"
    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))

    class _KaatuvaAgentti:
        @staticmethod
        def run(run_id, run_type, db_path):
            raise RuntimeError("simuloitu agenttivirhe")

    monkeypatch.setattr(orkestroija, "_AGENTIT", [("Testiagentti", _KaatuvaAgentti)])

    with pytest.raises(RuntimeError):
        orkestroija.run_normal(polku)

    yhteys = tietokanta.open_connection(polku)
    try:
        rivi = yhteys.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        yhteys.close()

    assert rivi["status"] == "keskeytyi"
    assert "simuloitu agenttivirhe" in rivi["error"]


def test_run_review_paattyy_valmis_tilaan_run_type_tarkistus(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))

    # Ei avoimia positioita eika ehdokkaita -> jokainen agentti palaa
    # siististi ilman verkkokutsuja (ei tarvetta monkeypatchata mitaan).
    ajo_id = orkestroija.run_review(polku)

    rivi = tietokanta.get_run(ajo_id, db_path=polku)
    assert rivi["status"] == "valmis"
    assert rivi["run_type"] == "tarkistus"


def test_vanha_kesken_ajo_siivotaan_ennen_uutta_ajoa(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))

    # jaljittelee ajoa joka katkesi kesken (kone nukkui, terminaali suljettiin)
    tietokanta.init_database(polku)
    tietokanta.start_run(
        run_id="vanha_kesken",
        run_type="normaali",
        settings_json="{}",
        model_version="1.0",
        db_path=polku,
    )

    # ei avoimia positioita eika ehdokkaita -> ei tarvetta monkeypatchata mitaan
    ajo_id = orkestroija.run_review(polku)

    vanha = tietokanta.get_run("vanha_kesken", db_path=polku)
    uusi = tietokanta.get_run(ajo_id, db_path=polku)

    # todistaa etta siivous ajetaan ennen start_run():ia: vanha rivi on
    # keskeytynyt JA uusi ajo on silti paatynyt tilaan 'valmis'.
    assert vanha["status"] == "keskeytyi"
    assert vanha["error"] == "ajo ei paattynyt (prosessi katkesi)"
    assert uusi["status"] == "valmis"


def test_git_komennon_epaonnistuessa_get_git_commit_palauttaa_none(monkeypatch):
    def _epaonnistu(*args, **kwargs):
        raise FileNotFoundError("git ei loydy")

    monkeypatch.setattr(orkestroija.subprocess, "run", _epaonnistu)

    assert orkestroija._get_git_commit() is None


# --- data-valmiustarkistus ennen ajoa (vaihe 2h) ---


def _rivimaara(polku, taulu):
    yhteys = tietokanta.open_connection(polku)
    try:
        return yhteys.execute(f"SELECT COUNT(*) FROM {taulu}").fetchone()[0]
    finally:
        yhteys.close()


def test_data_ei_valmis_runs_taulu_pysyy_muuttumattomana(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)
    monkeypatch.setattr(
        orkestroija, "_data_ready_now", lambda: (False, "markkina ei ole viela auki")
    )

    rivimaara_ennen = _rivimaara(polku, "runs")

    with pytest.raises(orkestroija.DataNotReadyError):
        orkestroija.run_normal(polku)

    rivimaara_jalkeen = _rivimaara(polku, "runs")
    assert rivimaara_ennen == 0
    assert rivimaara_jalkeen == 0


def test_data_ei_valmis_yhtaan_agenttia_ei_kutsuta(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    monkeypatch.setattr(
        orkestroija, "_data_ready_now", lambda: (False, "markkina ei ole viela auki")
    )

    kutsut = []

    class _Laskuri:
        @staticmethod
        def run(run_id, run_type, db_path):
            kutsut.append(run_id)

    monkeypatch.setattr(
        orkestroija,
        "_AGENTIT",
        [
            ("Testiagentti 1", _Laskuri),
            ("Testiagentti 2", _Laskuri),
        ],
    )

    with pytest.raises(orkestroija.DataNotReadyError):
        orkestroija.run_normal(polku)

    assert kutsut == []


def test_data_valmis_ajo_etenee_normaalisti(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))

    # Ei avoimia positioita eika ehdokkaita -> tarkistusajo palaa siististi
    # ilman verkkokutsuja.
    ajo_id = orkestroija.run_review(polku)

    rivi = tietokanta.get_run(ajo_id, db_path=polku)
    assert rivi["status"] == "valmis"


def test_regressio_is_bar_ready_koskematon_agentti_2_keskeyttaa_yha(
    tmp_path, monkeypatch
):
    # Ennakkotarkistus (is_data_ready) ei korvaa is_bar_ready():a - jos
    # ennakkotarkistus jotenkin paastaisi ajon lapi, Agentti 2:n oma tarkka
    # vartija keskeyttaa yha jos palkki on kesken.
    polku = tmp_path / "testi.db"
    monkeypatch.setattr(orkestroija, "_data_ready_now", lambda: (True, ""))

    def _lisaa_ehdokas(run_id, run_type, db_path):
        tietokanta.insert_candidate(
            run_id=run_id,
            run_type=run_type,
            symbol="AAPL",
            source="analyytikko",
            advanced=1,
            db_path=db_path,
        )
        return "1 ehdokas"

    monkeypatch.setattr(trendinmetsastaja, "run", _lisaa_ehdokas)

    def _kesken_oleva_palkki(symbol):
        raise tekninen_analyytikko.IncompletePriceDataError(
            f"{symbol}: viimeisin palkki on kuluva porssipaiva"
        )

    monkeypatch.setattr(tekninen_analyytikko, "fetch_price_data", _kesken_oleva_palkki)

    with pytest.raises(RuntimeError) as virhe:
        orkestroija.run_normal(polku)

    assert not isinstance(virhe.value, orkestroija.DataNotReadyError)
