"""Yhteiset pytest-fixturit."""

import logging

import pytest

from core import loki, tietokanta


@pytest.fixture(autouse=True)
def _erillinen_lokitiedosto(tmp_path, monkeypatch):
    """Ohjaa lokituksen tmp_path:iin, jottei testiajot kirjaudu data/loki.txt:iin."""
    root = logging.getLogger(loki._ROOT_NAME)
    vanhat_kasittelijat = list(root.handlers)
    for kasittelija in vanhat_kasittelijat:
        root.removeHandler(kasittelija)

    monkeypatch.setattr(loki, "LOG_FILE", tmp_path / "loki.txt")
    monkeypatch.setattr(loki, "_initialized", False)

    yield

    for kasittelija in list(root.handlers):
        root.removeHandler(kasittelija)
        kasittelija.close()
    for kasittelija in vanhat_kasittelijat:
        root.addHandler(kasittelija)


@pytest.fixture(autouse=True)
def _erillinen_arkisto(tmp_path, monkeypatch):
    """Ohjaa runs-metatiedoston (ja Agentti 1:n arkiston) tmp_path:iin.

    Ilman tätä start_run()/finish_run() kirjoittaisivat _ajo.json:in oikeaan
    data/arkisto/-hakemistoon aina kun testi kutsuu niitä (esim. orkestroijan
    kautta) - se on kirjoita-kerran-arkisto eikä testien kuulu koskea siihen.
    """
    monkeypatch.setattr(tietokanta, "ARKISTO_DIR", tmp_path / "arkisto")
