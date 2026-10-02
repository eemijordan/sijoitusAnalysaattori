"""Ajastetun ajon päätös: ajetaanko nyt, ja mitkä ajotyypit (vaihe 6a).

Windowsin Tehtävien ajoitus käynnistää tämän ti-la klo 10 Suomen aikaa
(vaihe 6b), ja jos kone ei ollut silloin päällä, heti kun kone seuraavan
kerran avataan. Tämä moduuli päättää, onko ajo vielä ajankohtainen:

- vain ajopäivinä (tarkistus ti-la, normaaliajo ti ja pe)
- ei ennen klo 10:tä: aiemman päivän väliin jäänyt ajo ei siirry seuraavalle
  päivälle, vaan sen myöhästynyt käynnistys ohitetaan
- ei New Yorkin pörssin avauduttua (porssiaika.is_data_ready)
- sama ajotyyppi vain kerran päivässä (myös käsin ajettu lasketaan)
- ei koskaan committaamattomalla agents/- tai core/-koodilla.
"""

import sqlite3
import subprocess
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from core import orkestroija, porssiaika, tietokanta
from core.ajolukko import RunLockedError

SCHEDULED_TIME = time(10, 0)  # Suomen aikaa
REVIEW_WEEKDAYS = frozenset({1, 2, 3, 4, 5})  # ti-la; date.weekday(): ma = 0
NORMAL_WEEKDAYS = frozenset({1, 4})  # ti ja pe
HELSINKI = ZoneInfo("Europe/Helsinki")
NEW_YORK = ZoneInfo("America/New_York")
_WEEKDAY_NAMES = ("ma", "ti", "ke", "to", "pe", "la", "su")
_CODE_DIRS = ("agents", "core")  # vain naiden muutokset vaikuttavat paatoksiin


def due_run_types(day: date) -> list[str]:
    """Palauttaa päivän ajotyypit ajojärjestyksessä: tarkistus ennen normaalia."""
    run_types = []
    if day.weekday() in REVIEW_WEEKDAYS:
        run_types.append("tarkistus")
    if day.weekday() in NORMAL_WEEKDAYS:
        run_types.append("normaali")
    return run_types


def decide_scheduled_run(
    now_fi: datetime, completed_today: set[str]
) -> tuple[list[str], str]:
    """Palauttaa (ajettavat ajotyypit, selitys). Tyhjä lista = ei ajeta nyt."""
    weekday = _WEEKDAY_NAMES[now_fi.weekday()]
    due = due_run_types(now_fi.date())
    if not due:
        return [], f"Tanaan ({weekday}) ei ole ajopaiva. Ajot ovat ti-la klo 10."
    if now_fi.time() < SCHEDULED_TIME:
        return [], (
            "Kello ei ole viela 10. Tama on aiemmin valiin jaaneen ajon "
            "myohastynyt kaynnistys, eika sita ajeta. Taman paivan ajo on klo 10."
        )
    ready, reason = porssiaika.is_data_ready(now_fi.astimezone(NEW_YORK))
    if not ready:
        return [], f"New Yorkin porssi on jo auki - paivan ajo jaa valiin ({reason})."
    remaining = [run_type for run_type in due if run_type not in completed_today]
    if not remaining:
        return [], "Taman paivan ajot on jo tehty."
    return remaining, f"Ajetaan: {', '.join(remaining)}."


def uncommitted_code_changes(cwd) -> list[str] | None:
    """Palauttaa agents/- ja core/-kansioiden committaamattomat muutokset.

    None jos git ei vastaa: silloin ajoa ei estetä, koska runs.git_commit
    kirjautuu silloin joka tapauksessa Noneksi eikä mikään väitä väärää.
    """
    try:
        tulos = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "--", *_CODE_DIRS],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception:
        return None
    return [rivi[3:] for rivi in tulos.stdout.splitlines() if rivi.strip()]


def _now_fi() -> datetime:
    """Nykyhetki Suomen aikaa (testit korvaavat tämän)."""
    return datetime.now(HELSINKI)


def main(argv: list[str]) -> int:
    """Ajaa tämän päivän erääntyneet ajot. Paluukoodit kuten orkestroijalla."""
    dry_run = "--kuiva" in argv
    now_fi = _now_fi()
    weekday = _WEEKDAY_NAMES[now_fi.weekday()]
    print(f"Ajastettu ajo {now_fi:%d.%m.%Y} klo {now_fi:%H:%M} ({weekday})")
    try:
        completed = tietokanta.get_completed_run_types(now_fi.date().isoformat())
    except sqlite3.OperationalError:
        completed = set()  # tuore kanta ilman runs-taulua
    run_types, explanation = decide_scheduled_run(now_fi, completed)
    print(explanation)
    if not run_types:
        return 0

    changes = uncommitted_code_changes(tietokanta.BASE_DIR)
    if changes:
        print("Koodissa on committaamattomia muutoksia, joten ajastettu ajo ei aja")
        print("keskeneraista koodia:")
        for change in changes:
            print(f"  {change}")
        print("Committaa muutokset tai aja kasin: scripts\\aja.cmd")
        return 1
    if dry_run:
        print("Kuiva-ajo: mitaan ei ajettu.")
        return 0

    for run_type in run_types:
        run = orkestroija.run_review
        if run_type == "normaali":
            run = orkestroija.run_normal
        try:
            run_id = run()
        except orkestroija.DataNotReadyError as error:
            print(f"DATA EI VALMIS: {error}")
            return 2
        except RunLockedError as error:
            print(f"TOINEN AJO KAYNNISSA: {error}")
            return 3
        except RuntimeError as error:
            print(f"VIRHE: {error}")
            return 1
        print(f"{run_type}: valmis (ajo {run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
