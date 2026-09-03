from core import tietokanta

KOVAKOODATUT_PISTEET = {
    "AAPL": 8,
    "MSFT": 6,
}
OLETUSPISTEET = 5


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    ehdokkaat = tietokanta.hae_ehdokkaat(ajo_id, vain_jatkoon=True, polku=polku)

    for ehdokas in ehdokkaat:
        pisteet = KOVAKOODATUT_PISTEET.get(ehdokas["symboli"], OLETUSPISTEET)
        tietokanta.lisaa_pisteytys(
            ajo_id=ajo_id,
            ajotyyppi=ajotyyppi,
            symboli=ehdokas["symboli"],
            risk_reward_pisteet=pisteet,
            polku=polku,
        )

    return f"{len(ehdokkaat)} pisteytysta kirjattu"
