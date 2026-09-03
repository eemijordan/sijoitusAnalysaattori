from core import loki, tietokanta

_loki = loki.hae_loki(__name__)


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    ehdokkaat = tietokanta.hae_ehdokkaat(ajo_id, vain_jatkoon=False, polku=polku)
    pisteytykset = tietokanta.hae_pisteytykset(ajo_id, polku=polku)
    paatokset = tietokanta.hae_paatokset(ajo_id, polku=polku)

    yhteenveto = (
        f"ajo {ajo_id} ({ajotyyppi}): {len(ehdokkaat)} ehdokasta, "
        f"{len(pisteytykset)} pisteytysta, {len(paatokset)} paatosta"
    )
    _loki.info(yhteenveto)
    return yhteenveto
