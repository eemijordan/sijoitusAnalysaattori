from core import loki, tietokanta

_loki = loki.get_logger(__name__)


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.DEFAULT_DB_PATH) -> str:
    ehdokkaat = tietokanta.get_candidates(ajo_id, advanced_only=False, db_path=polku)
    pisteytykset = tietokanta.get_scores(ajo_id, db_path=polku)
    paatokset = tietokanta.get_decisions(ajo_id, db_path=polku)

    yhteenveto = (
        f"ajo {ajo_id} ({ajotyyppi}): {len(ehdokkaat)} ehdokasta, "
        f"{len(pisteytykset)} pisteytysta, {len(paatokset)} paatosta"
    )
    _loki.info(yhteenveto)
    return yhteenveto
