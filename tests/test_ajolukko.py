"""Testit core/ajolukko.py:lle (vaihe 6a)."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.ajolukko import RunLockedError, run_lock

JUURI = Path(__file__).resolve().parent.parent


def test_lukko_estaa_toisen_samanaikaisen_lukon(tmp_path):
    polku = tmp_path / "ajo.lock"

    with run_lock(polku):
        with pytest.raises(RunLockedError):
            with run_lock(polku):
                pass


def test_lukko_vapautuu_with_lohkon_jalkeen(tmp_path):
    polku = tmp_path / "ajo.lock"

    with run_lock(polku):
        pass
    with run_lock(polku):
        pass


def test_lukko_vapautuu_poikkeuksen_jalkeen(tmp_path):
    polku = tmp_path / "ajo.lock"

    with pytest.raises(ValueError):
        with run_lock(polku):
            raise ValueError("simuloitu")
    with run_lock(polku):
        pass


def test_puuttuva_hakemisto_luodaan(tmp_path):
    polku = tmp_path / "ei" / "ole" / "ajo.lock"

    with run_lock(polku):
        assert polku.parent.is_dir()


def test_run_locked_error_on_runtime_errorin_aliluokka():
    assert issubclass(RunLockedError, RuntimeError)


def test_lukko_toimii_prosessien_valilla_ja_vapautuu_kun_prosessi_kuolee(tmp_path):
    # Tarkein testi: todistaa kayttojarjestelmakohtaisen haaran (Windowsissa
    # msvcrt) oikealla kayttojarjestelmalla - ja etta kayttojarjestelma
    # vapauttaa lukon itse kun prosessi tapetaan.
    polku = tmp_path / "ajo.lock"
    koodi = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(JUURI)!r})\n"
        "from core.ajolukko import run_lock\n"
        f"with run_lock({str(polku)!r}):\n"
        "    print('lukittu', flush=True)\n"
        "    time.sleep(60)\n"
    )
    prosessi = subprocess.Popen(
        [sys.executable, "-c", koodi], stdout=subprocess.PIPE, text=True
    )
    try:
        assert prosessi.stdout.readline().strip() == "lukittu"
        with pytest.raises(RunLockedError):
            with run_lock(polku):
                pass
    finally:
        prosessi.kill()
        prosessi.wait()
        prosessi.stdout.close()

    # Windowsissa vapautuminen voi viedä hetken.
    takaraja = time.monotonic() + 5
    while True:
        try:
            with run_lock(polku):
                break
        except RunLockedError:
            if time.monotonic() > takaraja:
                raise
            time.sleep(0.1)
