from datetime import datetime
from zoneinfo import ZoneInfo

from core import porssiaika

_ET = ZoneInfo("America/New_York")


def _hetki(vuosi, kk, pv, tunti, minuutti):
    return datetime(vuosi, kk, pv, tunti, minuutti, tzinfo=_ET)


def test_lauantai_on_valmis():
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 12, 12, 0))  # lauantai

    assert valmis is True
    assert syy == ""


def test_sunnuntai_on_valmis():
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 13, 12, 0))  # sunnuntai

    assert valmis is True
    assert syy == ""


def test_arkiaamuyo_kello_02_on_valmis():
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 9, 2, 0))  # keskiviikko

    assert valmis is True
    assert syy == ""


def test_arkipaiva_kello_09_29_on_valmis():
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 9, 9, 29))

    assert valmis is True
    assert syy == ""


def test_arkipaiva_kello_09_30_ei_ole_valmis():
    # Rajatapaus: porssi avautuu tasan 09:30, jolloin uusin palkki muuttuu
    # kuluvan paivan kesken olevaksi palkiksi.
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 9, 9, 30))

    assert valmis is False
    assert syy != ""


def test_arkipaiva_kello_14_porssi_auki_ei_ole_valmis():
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 9, 14, 0))

    assert valmis is False
    assert syy != ""


def test_arkipaiva_kello_20_porssi_kiinni_ei_ole_valmis():
    # Tama on se tapaus jonka vanha "porssi auki" -tarkistus nayttaisi
    # vaarin: porssi on jo sulkeutunut, mutta paiva ei ole viela vaihtunut
    # New Yorkissa, joten uusin palkki on yha kuluvan paivan kesken oleva
    # palkki.
    valmis, syy = porssiaika.is_data_ready(_hetki(2026, 9, 9, 20, 0))

    assert valmis is False
    assert syy != ""


def test_syy_teksti_ei_ole_tyhja_ja_kertoo_miksi():
    _, syy = porssiaika.is_data_ready(_hetki(2026, 9, 9, 14, 0))

    assert len(syy) > 0
    assert "2026-09-09" in syy
