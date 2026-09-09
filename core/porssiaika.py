"""Halpa ennakkotarkistus: onko edellisen paivan kurssidata jo lopullinen.

Oma pieni moduuli eika osa agents/tekninen_analyytikko.py:ta, jotta seka
core/orkestroija.py etta dashboard/nakyma.py voivat tuoda taman importtaamatta
yhtaan agenttia (CLAUDE.md: agentit eivat kommunikoi suoraan keskenaan, eika
orkestroija tai nakyma saa importtaa agentin sisaista logiikkaa taman
tarkistuksen takia).
"""

from datetime import datetime, time

# Pörssi avautuu 09:30 New Yorkin aikaa. Ennen tata uusin palkki on edellisen
# porssipaivan paatoskurssi (lopullinen); tasta hetkesta alkaen aina
# keskiyohon asti (myos porssin sulkeuduttua 16:00) uusin palkki olisi
# kuluvan paivan kesken oleva palkki - paiva ei vaihdu New Yorkissa ennen
# keskiyota.
MARKET_OPEN_ET = time(9, 30)


def is_data_ready(now_et: datetime) -> tuple[bool, str]:
    """Kertoo kellonajan perusteella onko edellisen päivän palkki jo lopullinen.

    Sama sääntö kuin agents/tekninen_analyytikko.py:n is_bar_ready(), mutta
    ilman API-kutsua: heti kun pörssi on avautunut, uusin palkki on kuluvan
    päivän kesken oleva palkki eikä dataa voi käyttää. Tämä on halpa
    ennakkotarkistus joka estää turhan työn (Agentti 1:n API-kutsut ja
    arkistokirjoitukset) ennen kuin mitään on tehty - se EI korvaa
    is_bar_ready():ta, joka katsoo oikeaa palkkia Twelve Datasta ja hoitaa
    pyhäpäivät. Tämä funktio ei huomioi pyhäpäiviä, aivan kuten nykyinen
    karkea tarkistus ei huominut - juuri siksi is_bar_ready() jää paikalleen
    tarkkana varmistuksena.
    """
    if now_et.weekday() >= 5:  # 5 = lauantai, 6 = sunnuntai
        return True, ""
    if now_et.time() < MARKET_OPEN_ET:
        return True, ""

    reason = (
        f"kuluva porssipaiva {now_et.date().isoformat()} on kesken "
        f"(New Yorkissa klo {now_et.strftime('%H:%M')}, porssi avautuu "
        f"{MARKET_OPEN_ET.strftime('%H:%M')}) - edellisen paivan paatoskurssi "
        f"ei ole viela viimeisin palkki"
    )
    return False, reason
