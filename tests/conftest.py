"""Yhteiset pytest-fixturit."""

import logging

import pytest

from core import loki


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
