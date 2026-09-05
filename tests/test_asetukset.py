from core import asetukset


def test_tilannekuva_sisaltaa_nykyiset_vakiot():
    tilannekuva = asetukset.snapshot_settings()

    assert tilannekuva["INITIAL_CAPITAL"] == asetukset.INITIAL_CAPITAL
    assert tilannekuva["RR_THRESHOLD"] == asetukset.RR_THRESHOLD
    assert tilannekuva["MIN_SHARE_PRICE"] == asetukset.MIN_SHARE_PRICE
    assert tilannekuva["MAX_PRICE_DRIFT"] == asetukset.MAX_PRICE_DRIFT
    assert tilannekuva["MODEL_VERSION"] == asetukset.MODEL_VERSION


def test_tilannekuva_ei_sisalla_funktioita_eika_moduuleja():
    tilannekuva = asetukset.snapshot_settings()

    assert "get_twelve_data_key" not in tilannekuva
    assert "get_fmp_key" not in tilannekuva
    assert "os" not in tilannekuva
    assert "Path" not in tilannekuva
    assert "BASE_DIR" not in tilannekuva


def test_uusi_vakio_ilmestyy_tilannekuvaan_automaattisesti(monkeypatch):
    monkeypatch.setattr(asetukset, "UUSI_TESTIVAKIO", 42, raising=False)

    tilannekuva = asetukset.snapshot_settings()

    assert tilannekuva["UUSI_TESTIVAKIO"] == 42
