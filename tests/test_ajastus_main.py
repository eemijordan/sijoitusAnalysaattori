"""Testit core/ajastus.py:n main()-funktiolle (vaihe 6a).

Kello, tietokanta, git ja orkestroija korvataan - mitaan ei oikeasti ajeta.
"""

from datetime import datetime

import pytest

from core import ajastus, orkestroija, tietokanta
from core.ajolukko import RunLockedError

TIISTAI_10 = datetime(2026, 10, 6, 10, 0, tzinfo=ajastus.HELSINKI)
MAANANTAI_12 = datetime(2026, 10, 12, 12, 0, tzinfo=ajastus.HELSINKI)


@pytest.fixture
def ymparisto(monkeypatch):
    """Korvaa riippuvuudet; palauttaa listan ajetuista ajotyypeistä."""
    ajetut = []
    monkeypatch.setattr(ajastus, "_now_fi", lambda: TIISTAI_10)
    monkeypatch.setattr(tietokanta, "get_completed_run_types", lambda day: set())
    monkeypatch.setattr(ajastus, "uncommitted_code_changes", lambda cwd: [])

    def _tarkistus():
        ajetut.append("tarkistus")
        return "ajo_t"

    def _normaali():
        ajetut.append("normaali")
        return "ajo_n"

    monkeypatch.setattr(orkestroija, "run_review", _tarkistus)
    monkeypatch.setattr(orkestroija, "run_normal", _normaali)
    return ajetut


def test_tiistaina_ajaa_tarkistuksen_ja_sitten_normaalin(ymparisto):
    assert ajastus.main([]) == 0
    assert ymparisto == ["tarkistus", "normaali"]


def test_kuiva_ajo_ei_aja_mitaan(ymparisto, capsys):
    assert ajastus.main(["--kuiva"]) == 0
    assert ymparisto == []
    assert "Kuiva-ajo" in capsys.readouterr().out


def test_committaamattomat_muutokset_estavat_ajon(ymparisto, monkeypatch, capsys):
    monkeypatch.setattr(
        ajastus, "uncommitted_code_changes", lambda cwd: ["core/ajastus.py"]
    )

    assert ajastus.main([]) == 1
    assert ymparisto == []
    assert "core/ajastus.py" in capsys.readouterr().out


@pytest.mark.parametrize(
    "poikkeus, koodi",
    [
        (RunLockedError("varattu"), 3),
        (RuntimeError("kaatui"), 1),
        (orkestroija.DataNotReadyError("ei valmis"), 2),
    ],
)
def test_tarkistuksen_virhe_palauttaa_koodin_eika_normaalia_ajeta(
    ymparisto, monkeypatch, poikkeus, koodi
):
    def _kaatuva():
        ymparisto.append("tarkistus")
        raise poikkeus

    monkeypatch.setattr(orkestroija, "run_review", _kaatuva)

    assert ajastus.main([]) == koodi
    assert ymparisto == ["tarkistus"]


def test_maanantaina_ei_ajeta_mitaan(ymparisto, monkeypatch):
    monkeypatch.setattr(ajastus, "_now_fi", lambda: MAANANTAI_12)

    assert ajastus.main([]) == 0
    assert ymparisto == []
