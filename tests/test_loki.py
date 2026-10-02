"""Testit core/loki.py:n API-avainten peittämiselle (vaihe 6b)."""

from core import loki


def test_peittaa_url_kyselyparametrin():
    teksti = (
        "401 Client Error for url: "
        "https://api.twelvedata.com/stocks?country=United+States&apikey=abc123XYZ"
    )

    tulos = loki.mask_secrets(teksti)

    assert tulos.endswith("country=United+States&apikey=***")
    assert "abc123XYZ" not in tulos


def test_peittaa_max_retries_viestin_ja_sailyttaa_sulkumerkin():
    teksti = (
        "Max retries exceeded with url: /stable/x?page=0&apikey=abc123 (Caused by X)"
    )

    tulos = loki.mask_secrets(teksti)

    assert tulos == (
        "Max retries exceeded with url: /stable/x?page=0&apikey=*** (Caused by X)"
    )


def test_peittaa_ymparistomuuttujan_arvon(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "abcd1234efgh5678")

    tulos = loki.mask_secrets("{'apikey': 'abcd1234efgh5678'}")

    assert tulos == "{'apikey': '***'}"


def test_none_ja_teksti_ilman_avainta():
    assert loki.mask_secrets(None) is None
    assert loki.mask_secrets("tavallinen lokirivi") == "tavallinen lokirivi"


def test_lokirivi_peittyy_lokitiedostossa():
    # conftest ohjaa LOG_FILE:n tmp_path:iin.
    loki.get_logger("testi").error("haku epaonnistui: %s", "x?apikey=salainen123")
    for kasittelija in loki.logging.getLogger(loki._ROOT_NAME).handlers:
        kasittelija.flush()

    sisalto = loki.LOG_FILE.read_text(encoding="utf-8")
    assert "apikey=***" in sisalto
    assert "salainen123" not in sisalto
