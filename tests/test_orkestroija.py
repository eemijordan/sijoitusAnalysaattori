from datetime import date, datetime, timedelta, timezone

from agents import trendinmetsastaja
from core import orkestroija, tietokanta


def test_aja_normaali_paasta_paahan(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"

    tuore_iso = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tuore_pvm = (date.today() - timedelta(days=5)).isoformat()

    monkeypatch.setattr(
        trendinmetsastaja,
        "hae_analyytikkomuutokset",
        lambda: [
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
        "hae_kongressikaupat",
        lambda: [
            {
                "symbol": "MSFT",
                "transactionDate": tuore_pvm,
                "type": "Purchase",
                "amount": "$100,001 - $250,000",
            },
        ],
    )
    monkeypatch.setattr(trendinmetsastaja, "hae_sisapiirikaupat", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "hae_tavoitehinnat", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "hae_us_symbolit", lambda: {"AAPL", "MSFT"})

    ajo_id = orkestroija.aja_normaali(polku)

    ehdokkaat = tietokanta.hae_ehdokkaat(ajo_id, vain_jatkoon=False, polku=polku)
    pisteytykset = tietokanta.hae_pisteytykset(ajo_id, polku=polku)
    paatokset = tietokanta.hae_paatokset(ajo_id, polku=polku)

    assert len(ehdokkaat) > 0
    assert len(pisteytykset) > 0
    assert len(paatokset) > 0

    for rivi in ehdokkaat + pisteytykset + paatokset:
        assert rivi["ajo_id"] == ajo_id
        assert rivi["ajotyyppi"] == "normaali"
