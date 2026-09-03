from core import tietokanta
from core.asetukset import RR_KYNNYS


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    pisteytykset = tietokanta.hae_pisteytykset(ajo_id, polku=polku)

    for pisteytys in pisteytykset:
        pisteet = pisteytys["risk_reward_pisteet"]
        if pisteet >= RR_KYNNYS:
            paatos = "osta"
            perustelu = f"risk_reward_pisteet {pisteet} >= RR_KYNNYS {RR_KYNNYS}"
        else:
            paatos = "hylatty"
            perustelu = f"risk_reward_pisteet {pisteet} < RR_KYNNYS {RR_KYNNYS}"

        tietokanta.lisaa_paatos(
            ajo_id=ajo_id,
            ajotyyppi=ajotyyppi,
            symboli=pisteytys["symboli"],
            paatos=paatos,
            perustelu=perustelu,
            polku=polku,
        )

    return f"{len(pisteytykset)} paatosta kirjattu"
