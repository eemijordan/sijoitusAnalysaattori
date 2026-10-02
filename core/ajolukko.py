"""Ajolukko: estää kaksi samanaikaista ajoa samaan tietokantaan (vaihe 6a).

Lukko on käyttöjärjestelmän tiedostolukko (Windowsissa msvcrt.locking,
muualla fcntl.flock) tiedostoon ajo.lock tietokannan vieressä. Käyttö-
järjestelmä vapauttaa lukon itse, kun prosessi päättyy - myös jos se
kaatuu tai ikkuna suljetaan kesken ajon - joten vanhentunutta lukkoa ei
tarvitse siivota käsin eikä arvata ajan perusteella.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_FILENAME = "ajo.lock"


class RunLockedError(RuntimeError):
    """Nostetaan kun toinen ajo pitää lukkoa - kieltäytyminen, ei kaatuminen."""


def _lukitse(tiedosto) -> None:
    """Yrittää lukita tiedoston heti; nostaa OSError jos lukko on varattu."""
    if os.name == "nt":
        import msvcrt

        tiedosto.seek(0)
        msvcrt.locking(tiedosto.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(tiedosto.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _vapauta(tiedosto) -> None:
    """Vapauttaa lukon. Virheet ohitetaan: sulkeminen vapauttaa joka tapauksessa."""
    try:
        if os.name == "nt":
            import msvcrt

            tiedosto.seek(0)
            msvcrt.locking(tiedosto.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(tiedosto.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def run_lock(lock_path: Path) -> Iterator[None]:
    """Pitää ajolukkoa with-lohkon ajan; nostaa RunLockedError jos varattu."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" eikä "w": avaaminen ei saa tyhjentää tiedostoa, jonka toinen
    # prosessi on lukinnut (Windowsissa se voisi epäonnistua).
    tiedosto = open(lock_path, "a+")
    try:
        try:
            _lukitse(tiedosto)
        except OSError as virhe:
            raise RunLockedError(
                "toinen ajo on kaynnissa (lukko varattu) - odota etta se valmistuu"
            ) from virhe
        try:
            yield
        finally:
            _vapauta(tiedosto)
    finally:
        tiedosto.close()
