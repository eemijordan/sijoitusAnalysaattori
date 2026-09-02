from core import orkestroija, tietokanta


def test_aja_normaali_paasta_paahan(tmp_path):
    polku = tmp_path / "testi.db"

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
