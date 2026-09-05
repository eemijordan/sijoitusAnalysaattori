import json
from datetime import date, datetime, timedelta, timezone

import pytest

from agents import tekninen_analyytikko, trendinmetsastaja
from core import orkestroija, tietokanta


def test_aja_normaali_paasta_paahan(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"

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


def test_kaatuva_agentti_jattaa_ajon_keskeytyi_tilaan_ja_virhetekstin(
    tmp_path, monkeypatch
):
    polku = tmp_path / "testi.db"

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


def test_run_review_paattyy_valmis_tilaan_run_type_tarkistus(tmp_path):
    polku = tmp_path / "testi.db"

    # Ei avoimia positioita eika ehdokkaita -> jokainen agentti palaa
    # siististi ilman verkkokutsuja (ei tarvetta monkeypatchata mitaan).
    ajo_id = orkestroija.run_review(polku)

    rivi = tietokanta.get_run(ajo_id, db_path=polku)
    assert rivi["status"] == "valmis"
    assert rivi["run_type"] == "tarkistus"


def test_git_komennon_epaonnistuessa_get_git_commit_palauttaa_none(monkeypatch):
    def _epaonnistu(*args, **kwargs):
        raise FileNotFoundError("git ei loydy")

    monkeypatch.setattr(orkestroija.subprocess, "run", _epaonnistu)

    assert orkestroija._get_git_commit() is None
