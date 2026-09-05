"""Ajaa neljä agenttia peräkkäin ja keskeyttää ajon jos jokin epäonnistuu."""

import json
import subprocess
import sys

from agents import paatoksentekija, raportoija, tekninen_analyytikko, trendinmetsastaja
from core import asetukset, loki, tietokanta

_loki = loki.get_logger(__name__)

_AGENTIT = [
    ("Agentti 1 - Trendinmetsastaja", trendinmetsastaja),
    ("Agentti 2 - Tekninen analyytikko", tekninen_analyytikko),
    ("Agentti 3 - Paatoksentekija", paatoksentekija),
    ("Agentti 4 - Raportoija", raportoija),
]


def _get_git_commit() -> str | None:
    """Hakee nykyisen git-commitin ajon toistettavuutta varten.

    Palauttaa None jos komento epäonnistuu (ei git-repoa, git puuttuu,
    mikä tahansa muu virhe) - commitin puuttuminen ei saa kaataa ajoa,
    koska se on vain lisätieto eikä ajon kannalta pakollinen.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tietokanta.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return None
    return result.stdout.strip()


def _run_agents(run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Alustaa tietokannan ja ajaa kaikki neljä agenttia järjestyksessä.

    Yhteinen run_normal():n ja run_review():n välillä, jottei runs-taulun
    kirjaus, virheenkäsittely ja agenttisilmukka eriydy kahteen paikkaan ja
    ala hiljalleen erota toisistaan ensimmäisen muutoksen jälkeen. Nostaa
    RuntimeErrorin jos jokin agentti epäonnistuu - loput agentit jäävät
    silloin ajamatta. Ajo kirjataan aina runs-tauluun: 'kesken' heti
    alussa, ja lopuksi 'valmis' tai 'keskeytyi' virheteksteineen - poikkeus
    nousee silti edelleen läpi kirjaamisen jälkeen.
    """
    run_id = tietokanta.new_run_id()

    tietokanta.init_database(db_path)
    tietokanta.start_run(
        run_id=run_id,
        run_type=run_type,
        settings_json=json.dumps(asetukset.snapshot_settings()),
        model_version=asetukset.MODEL_VERSION,
        git_commit=_get_git_commit(),
        db_path=db_path,
    )
    _loki.info(f"Ajo {run_id} ({run_type}) alkaa")

    try:
        for name, module in _AGENTIT:
            try:
                summary = module.run(run_id, run_type, db_path=db_path)
                _loki.info(f"{name}: {summary}")
            except Exception as error:
                _loki.error(f"{name} epaonnistui ajossa {run_id}: {error}")
                raise RuntimeError(
                    f"Ajo {run_id} keskeytyi: {name} epaonnistui - {error}"
                ) from error
    except Exception as run_error:
        tietokanta.finish_run(
            run_id, "keskeytyi", error=str(run_error), db_path=db_path
        )
        raise

    tietokanta.finish_run(run_id, "valmis", db_path=db_path)
    _loki.info(f"Ajo {run_id} ({run_type}) valmis")
    return run_id


def run_normal(db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Ajaa normaaliajon: etsii uusia ehdokkaita ja pisteyttää/ostaa niitä."""
    return _run_agents("normaali", db_path=db_path)


def run_review(db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Ajaa tarkistusajon: pisteyttää avoimet positiot ja päättää pito/myynti."""
    return _run_agents("tarkistus", db_path=db_path)


if __name__ == "__main__":
    _run_type = sys.argv[1] if len(sys.argv) > 1 else "normaali"
    _ajo_funktio = run_review if _run_type == "tarkistus" else run_normal
    try:
        _ajo_funktio()
    except RuntimeError as error:
        print(f"VIRHE: {error}")
        sys.exit(1)
