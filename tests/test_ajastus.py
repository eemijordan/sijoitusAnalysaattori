"""Testit core/ajastus.py:n puhtaalle paatoslogiikalle (vaihe 6a)."""

import subprocess
from datetime import date, datetime

import pytest

from core import ajastus

# 6.10.2026 ti, 7.10. ke, 8.10. to, 9.10. pe, 10.10. la, 11.10. su, 12.10. ma


def _fi(paiva, kuukausi, tunti, minuutti=0):
    return datetime(2026, kuukausi, paiva, tunti, minuutti, tzinfo=ajastus.HELSINKI)


@pytest.mark.parametrize(
    "paiva, odotus",
    [
        (date(2026, 10, 6), ["tarkistus", "normaali"]),  # ti
        (date(2026, 10, 7), ["tarkistus"]),  # ke
        (date(2026, 10, 8), ["tarkistus"]),  # to
        (date(2026, 10, 9), ["tarkistus", "normaali"]),  # pe
        (date(2026, 10, 10), ["tarkistus"]),  # la
        (date(2026, 10, 11), []),  # su
        (date(2026, 10, 12), []),  # ma
    ],
)
def test_due_run_types_viikonpaivittain(paiva, odotus):
    assert ajastus.due_run_types(paiva) == odotus


def test_tiistaina_klo_10_molemmat_tarkistus_ensin():
    ajot, _ = ajastus.decide_scheduled_run(_fi(6, 10, 10), set())

    assert ajot == ["tarkistus", "normaali"]


def test_ennen_klo_10_ei_ajeta():
    ajot, selitys = ajastus.decide_scheduled_run(_fi(7, 10, 9, 59), set())

    assert ajot == []
    assert "10" in selitys


def test_maanantaina_ei_ajopaiva():
    ajot, selitys = ajastus.decide_scheduled_run(_fi(12, 10, 12), set())

    assert ajot == []
    assert "ei ole ajopaiva" in selitys


def test_myohastynyt_kaynnistys_samana_paivana_ajetaan():
    ajot, _ = ajastus.decide_scheduled_run(_fi(7, 10, 14), set())

    assert ajot == ["tarkistus"]


def test_porssin_avauduttua_ei_ajeta():
    # ke klo 16.31 Suomessa = 9.31 New Yorkissa
    ajot, selitys = ajastus.decide_scheduled_run(_fi(7, 10, 16, 31), set())

    assert ajot == []
    assert "porssi" in selitys


def test_kesaaikaero_lokakuun_lopussa():
    # Suomi siirtyy talviaikaan 25.10., New York vasta 1.11. - ero on
    # 6 tuntia, joten porssi aukeaa jo klo 15.30 Suomen aikaa.
    myohassa, _ = ajastus.decide_scheduled_run(_fi(27, 10, 15, 45), set())
    ajoissa, _ = ajastus.decide_scheduled_run(_fi(27, 10, 15), set())

    assert myohassa == []  # New Yorkissa 9.45
    assert ajoissa == ["tarkistus", "normaali"]  # New Yorkissa 9.00


def test_jo_ajettu_tyyppi_pudotetaan():
    ajot, _ = ajastus.decide_scheduled_run(_fi(6, 10, 10), {"tarkistus"})

    assert ajot == ["normaali"]


def test_molemmat_jo_ajettu():
    ajot, selitys = ajastus.decide_scheduled_run(
        _fi(6, 10, 10), {"tarkistus", "normaali"}
    )

    assert ajot == []
    assert "jo tehty" in selitys


def _korvaa_git(monkeypatch, stdout=None, virhe=None):
    def _run(*args, **kwargs):
        if virhe is not None:
            raise virhe
        return subprocess.CompletedProcess(args[0], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ajastus.subprocess, "run", _run)


def test_uncommitted_muutokset_polkulistana(monkeypatch):
    _korvaa_git(monkeypatch, " M agents/paatoksentekija.py\n?? core/uusi.py\n")

    assert ajastus.uncommitted_code_changes(".") == [
        "agents/paatoksentekija.py",
        "core/uusi.py",
    ]


def test_uncommitted_puhdas_tyhja_lista(monkeypatch):
    _korvaa_git(monkeypatch, "")

    assert ajastus.uncommitted_code_changes(".") == []


def test_uncommitted_git_virhe_none(monkeypatch):
    _korvaa_git(monkeypatch, virhe=subprocess.CalledProcessError(128, "git"))

    assert ajastus.uncommitted_code_changes(".") is None
