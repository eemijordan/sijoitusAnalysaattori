"""Agentti 4: kokoaa ajon yhteenvedon lokiin (runko, Excel tulossa myöhemmin)."""

from core import loki, tietokanta

_loki = loki.get_logger(__name__)


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Lukee ajon ehdokkaat, pisteytykset ja päätökset ja lokittaa määrät."""
    candidates = tietokanta.get_candidates(run_id, advanced_only=False, db_path=db_path)
    scores = tietokanta.get_scores(run_id, db_path=db_path)
    decisions = tietokanta.get_decisions(run_id, db_path=db_path)

    summary = (
        f"ajo {run_id} ({run_type}): {len(candidates)} ehdokasta, "
        f"{len(scores)} pisteytysta, {len(decisions)} paatosta"
    )
    _loki.info(summary)
    return summary
