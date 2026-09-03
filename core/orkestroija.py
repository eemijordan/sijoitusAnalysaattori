import sys

from agents import paatoksentekija, raportoija, tekninen_analyytikko, trendinmetsastaja
from core import loki, tietokanta

_loki = loki.get_logger(__name__)

_AGENTIT = [
    ("Agentti 1 - Trendinmetsastaja", trendinmetsastaja),
    ("Agentti 2 - Tekninen analyytikko", tekninen_analyytikko),
    ("Agentti 3 - Paatoksentekija", paatoksentekija),
    ("Agentti 4 - Raportoija", raportoija),
]


def run_normal(db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    run_id = tietokanta.new_run_id()
    run_type = "normaali"

    tietokanta.init_database(db_path)
    _loki.info(f"Ajo {run_id} ({run_type}) alkaa")

    for name, module in _AGENTIT:
        try:
            # HUOM: agentit ottavat toistaiseksi vastaan aja(ajo_id, ajotyyppi,
            # polku=...) - nimet vaihtuvat run(run_id, run_type, db_path=...)
            # -muotoon askeleessa 3, jolloin tama kutsu paivitetaan samalla.
            summary = module.aja(run_id, run_type, polku=db_path)
            _loki.info(f"{name}: {summary}")
        except Exception as error:
            _loki.error(f"{name} epaonnistui ajossa {run_id}: {error}")
            raise RuntimeError(
                f"Ajo {run_id} keskeytyi: {name} epaonnistui - {error}"
            ) from error

    _loki.info(f"Ajo {run_id} ({run_type}) valmis")
    return run_id


if __name__ == "__main__":
    try:
        run_normal()
    except RuntimeError as error:
        print(f"VIRHE: {error}")
        sys.exit(1)
