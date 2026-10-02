"""Vartijatestit ajastuksen skripteille (vaihe 6b).

Käyttäjän ehdottomat vaatimukset (kone ei herää, ajo vain kirjautuneena,
ikkuna näkyy aina) ovat tekstiä PowerShell-skripteissä - nämä testit
estävät rikkomasta niitä myöhemmin vahingossa.
"""

from pathlib import Path

from core.ajastus import REVIEW_WEEKDAYS, SCHEDULED_TIME

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_PAIVAT = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _lue(nimi):
    return (SCRIPTS / nimi).read_text(encoding="utf-8")


def test_asennus_ei_heratea_konetta_ja_vaatii_kirjautumisen():
    teksti = _lue("ajastus_asenna.ps1")

    assert "-WakeToRun" not in teksti
    for vaadittu in (
        "-StartWhenAvailable",
        "-AllowStartIfOnBatteries",
        "-LogonType Interactive",
        "-RunLevel Limited",
    ):
        assert vaadittu in teksti


def test_asennuksen_paivat_ja_kellonaika_tasmaavat_ajastukseen():
    teksti = _lue("ajastus_asenna.ps1")
    paivat = ", ".join(_PAIVAT[paiva] for paiva in sorted(REVIEW_WEEKDAYS))

    assert paivat == "Tuesday, Wednesday, Thursday, Friday, Saturday"
    assert paivat in teksti
    assert f'-At "{SCHEDULED_TIME:%H:%M}"' in teksti
    # Paikallinen aika ilman Z:aa, muuten ajo siirtyisi talviajassa klo 9:aan.
    assert (
        f"Get-Date -Hour {SCHEDULED_TIME.hour} -Minute {SCHEDULED_TIME.minute}"
        in teksti
    )
    assert "InvariantCulture" in teksti


def test_ajastettu_skripti_nakyy_ja_odottaa_kayttajaa():
    teksti = _lue("ajastettu.ps1")

    assert "-m core.ajastus" in teksti
    assert "Read-Host" in teksti
    assert "TreatControlCAsInput" in teksti
    assert "Hidden" not in teksti
