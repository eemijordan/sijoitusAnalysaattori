from core import loki, tietokanta

_loki = loki.get_logger(__name__)


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    candidates = tietokanta.get_candidates(run_id, advanced_only=False, db_path=db_path)
    scores = tietokanta.get_scores(run_id, db_path=db_path)
    decisions = tietokanta.get_decisions(run_id, db_path=db_path)

    summary = (
        f"ajo {run_id} ({run_type}): {len(candidates)} ehdokasta, "
        f"{len(scores)} pisteytysta, {len(decisions)} paatosta"
    )
    _loki.info(summary)
    return summary


def aja(ajo_id, ajotyyppi, polku=tietokanta.DEFAULT_DB_PATH) -> str:
    # Valiaikainen taaksepainyhteensopiva ohitus - orkestroija kutsuu
    # toistaiseksi kaikkia agentteja nimella aja(polku=...), koska agentit
    # nimetaan uudelleen yksi kerrallaan (vaihe R, askel 3). Poistetaan kun
    # viimeinenkin agentti on nimetty ja orkestroija kutsuu run():ia suoraan.
    return run(ajo_id, ajotyyppi, db_path=polku)
