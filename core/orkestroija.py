"""Ajaa neljä agenttia peräkkäin ja keskeyttää ajon jos jokin epäonnistuu."""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents import paatoksentekija, raportoija, tekninen_analyytikko, trendinmetsastaja
from core import asetukset, loki, porssiaika, tietokanta
from core.ajolukko import LOCK_FILENAME, RunLockedError, run_lock

_loki = loki.get_logger(__name__)

_AGENTIT = [
    ("Agentti 1 - Trendinmetsastaja", trendinmetsastaja),
    ("Agentti 2 - Tekninen analyytikko", tekninen_analyytikko),
    ("Agentti 3 - Paatoksentekija", paatoksentekija),
    ("Agentti 4 - Raportoija", raportoija),
]


class DataNotReadyError(RuntimeError):
    """Nostetaan kun markkinadata ei viela ole valmista - ei ohjelmointivirhe.

    Eri poikkeustyyppi kuin ajon aikaiset RuntimeError-kaatumiset, jotta
    __main__-lohko (ja dashboard/nakyma.py paluukoodin kautta) voi erottaa
    kieltaytymisen kaatumisesta - kieltaytyminen ei ole ajo eika saa nayttaa
    samalta kuin jarjestelman hajoaminen.
    """


def _data_ready_now() -> tuple[bool, str]:
    """Ohut kääre porssiaika.is_data_ready():n ympärillä nykyhetkellä.

    Testit korvaavat tämän suoraan sen sijaan että joutuisivat odottamaan
    tai jäädyttämään oikeaa New Yorkin kelloa.
    """
    return porssiaika.is_data_ready(datetime.now(ZoneInfo("America/New_York")))


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
    """Ottaa ajolukon ja ajaa agentit; nostaa RunLockedError jos varattu.

    Lukko otetaan ennen init_database():a ja mark_stale_runs_interrupted():a
    (vaihe 6a): kun lukko on saatu, jokainen 'kesken'-ajo on oikeasti
    kuollut, joten siivous ei voi koskaan merkitä käynnissä olevaa ajoa
    keskeytyneeksi.
    """
    with run_lock(Path(db_path).parent / LOCK_FILENAME):
        return _run_agents_locked(run_type, db_path=db_path)


def _run_agents_locked(run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Alustaa tietokannan ja ajaa kaikki neljä agenttia järjestyksessä.

    Yhteinen run_normal():n ja run_review():n välillä, jottei runs-taulun
    kirjaus, virheenkäsittely ja agenttisilmukka eriydy kahteen paikkaan ja
    ala hiljalleen erota toisistaan ensimmäisen muutoksen jälkeen. Nostaa
    RuntimeErrorin jos jokin agentti epäonnistuu - loput agentit jäävät
    silloin ajamatta. Ajo kirjataan aina runs-tauluun: 'kesken' heti
    alussa, ja lopuksi 'valmis' tai 'keskeytyi' virheteksteineen - poikkeus
    nousee silti edelleen läpi kirjaamisen jälkeen.

    Nostaa DataNotReadyError:in eika kirjaa mitaan runs-tauluun, jos
    markkinadata ei viela ole valmista (ks. porssiaika.is_data_ready) -
    kieltaytyminen ei ole ajo, joten sita ei kirjata alkaneeksi.
    """
    added_columns = tietokanta.init_database(db_path)
    if added_columns:
        _loki.info(f"Kanta paivitetty: lisatty sarakkeet {', '.join(added_columns)}")

    # Ennen start_run():ia, ei sen jalkeen - toisin pain tama merkitsisi juuri
    # alkaneen ajon keskeytyneeksi. Siivoaa ajot jotka jaivat tilaan 'kesken'
    # koska prosessi katkesi (kone nukkui, terminaali suljettiin, kaatui).
    # Ei liity data-valmiustarkistukseen alla - ajetaan aina.
    stale_count = tietokanta.mark_stale_runs_interrupted(db_path)
    if stale_count > 0:
        _loki.warning(
            f"{stale_count} paattymatta jaanytta ajoa merkitty keskeytyneeksi"
        )

    # Halpa ennakkotarkistus ENNEN run_id:ta ja start_run():ia - vartija
    # istuu muuten Agentti 2:n sisalla symbolikohtaisesti, jolloin Agentti 1
    # ehtii jo tehda taydet API-kutsut ja arkistokirjoitukset ennen kuin
    # tulos heitetaan pois. Ei runs-rivia, ei API-kutsuja, ei arkistoa, ei
    # orpoja ehdokasriveja - ajoa ei aloitettu, joten sita ei kirjata
    # aloitetuksi. Ei korvaa is_bar_ready():ta, joka jaa Agentti 2:een
    # tarkkana varmistuksena (katsoo oikeaa palkkia, hoitaa pyhapaivat).
    ready, syy = _data_ready_now()
    if not ready:
        _loki.warning(f"Ajoa ei kaynnisteta: {syy}")
        raise DataNotReadyError(syy)

    run_id = tietokanta.new_run_id()

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


def main(argv: list[str]) -> int:
    """Ajaa argumentin mukaisen ajon ja palauttaa paluukoodin.

    0 valmis, 1 kaatui, 2 data ei valmis, 3 toinen ajo käynnissä. 2 ja 3
    ovat kieltäytymisiä, eivät kaatumisia, ja niiden pitää näyttää
    erilaisilta jokaiselle joka runs-taulua tai paluukoodia myöhemmin
    katsoo (ks. dashboard/nakyma.py).
    """
    run_type = argv[0] if argv else "normaali"
    run = run_review if run_type == "tarkistus" else run_normal
    # Aliluokat (DataNotReadyError, RunLockedError) ennen yleista
    # RuntimeError-haaraa.
    try:
        run()
    except DataNotReadyError as error:
        print(f"DATA EI VALMIS: {error}")
        return 2
    except RunLockedError as error:
        print(f"TOINEN AJO KAYNNISSA: {error}")
        return 3
    except RuntimeError as error:
        print(f"VIRHE: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
