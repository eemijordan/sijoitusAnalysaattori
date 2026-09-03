from core import loki, tietokanta
from core.asetukset import (
    ALKUPAAOMA,
    MAX_HINTAPOIKKEAMA,
    MAX_POSITIOT,
    POSITION_KOKO,
    RR_KYNNYS,
    SALKUN_STOP_TASO,
)

_loki = loki.hae_loki(__name__)


def paata_osto(pisteytys: dict, ehdokas: dict | None, tila: dict) -> tuple[str, str]:
    symboli = pisteytys.get("symboli")
    hinta = pisteytys.get("hinta")

    # 1. hinta puuttuu tai on virheellinen - ilman sita osakemaaraa ei voi
    # laskea eika ehtoa 4 tarkistaa.
    if hinta is None or hinta <= 0:
        return (
            "hylatty",
            f"hinta puuttuu tai on virheellinen ({hinta!r}), "
            f"ehtoa 4 ei voida tarkistaa",
        )

    # 2. duplikaatti - sama symboli ei saa olla aktiivisena kahdesti.
    if symboli in tila["aktiiviset_symbolit"]:
        return "hylatty", f"{symboli} on jo aktiivinen positio (duplikaatti)"

    # 3. RR-kynnys.
    rr = pisteytys.get("risk_reward_pisteet")
    if rr is None or rr < RR_KYNNYS:
        return "hylatty", f"risk_reward_pisteet {rr} < RR_KYNNYS {RR_KYNNYS}"

    # 4. hinta ei saa olla karannut liian kauas viiteostohinnasta. Jos
    # viiteostohintaa ei ole, ehto ohitetaan (ei hylata) ja se nakyy
    # perustelussa - tama ei ole hiljainen huononeminen.
    viiteostohinta = ehdokas.get("viiteostohinta") if ehdokas else None
    hintaehto_teksti = "viiteostohinta puuttuu, ehto 4 ohitettu"
    if viiteostohinta is not None:
        poikkeama = (hinta - viiteostohinta) / viiteostohinta
        poikkeama_pct = poikkeama * 100
        raja_pct = MAX_HINTAPOIKKEAMA * 100
        if poikkeama > 0:
            hintaehto_teksti = (
                f"hinta {hinta:.2f} on {poikkeama_pct:.1f} % yli viiteostohinnan "
                f"{viiteostohinta:.2f} (raja {raja_pct:.1f} %)"
            )
        else:
            hintaehto_teksti = (
                f"hinta {hinta:.2f} on {abs(poikkeama_pct):.1f} % alle viiteostohinnan "
                f"{viiteostohinta:.2f} (raja {raja_pct:.1f} %)"
            )
        if abs(poikkeama) > MAX_HINTAPOIKKEAMA:
            return "hylatty", hintaehto_teksti

    # 5. kapasiteetti.
    if tila["aktiivisia"] >= MAX_POSITIOT:
        return (
            "hylatty",
            f"aktiivisia positioita {tila['aktiivisia']} "
            f">= MAX_POSITIOT {MAX_POSITIOT}",
        )

    # 6. kateinen.
    if tila["kateinen"] < POSITION_KOKO:
        return (
            "hylatty",
            f"kateinen {tila['kateinen']:.2f} < POSITION_KOKO {POSITION_KOKO}",
        )

    perustelu = (
        f"risk_reward_pisteet {rr} >= RR_KYNNYS {RR_KYNNYS}; {hintaehto_teksti}; "
        f"tilaa {tila['aktiivisia']}/{MAX_POSITIOT} positiota, "
        f"kateista {tila['kateinen']:.2f} USD"
    )
    return "osta", perustelu


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    if ajotyyppi != "normaali":
        viesti = (
            f"ajotyyppi '{ajotyyppi}': pito/myynti-logiikkaa ei ole viela toteutettu "
            f"(vaihe 3b)"
        )
        _loki.info(viesti)
        return viesti

    pisteytykset = tietokanta.hae_pisteytykset(ajo_id, polku=polku)
    if not pisteytykset:
        yhteenveto = "0 pisteytysta, 0 paatosta"
        _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
        return yhteenveto

    ehdokasrivit = tietokanta.hae_ehdokkaat(ajo_id, vain_jatkoon=False, polku=polku)
    ehdokkaat: dict = {}
    for rivi in ehdokasrivit:
        if rivi["symboli"] not in ehdokkaat:
            ehdokkaat[rivi["symboli"]] = rivi

    aktiiviset = tietokanta.hae_aktiiviset_positiot(polku=polku)
    kateinen = tietokanta.hae_kateinen(polku=polku)

    # Salkkutason pysaytin ennen silmukkaa. Huom: mittari huomioi vain
    # toteutuneet tappiot (kateinen + avointen positioiden panokset), koska
    # avointen positioiden nykyhintoja ei ole saatavilla ilman API-kutsuja.
    toteutunut_salkun_arvo = kateinen + sum(p["panos_usd"] for p in aktiiviset)
    if toteutunut_salkun_arvo < SALKUN_STOP_TASO * ALKUPAAOMA:
        perustelu = (
            f"salkkutason pysaytin: toteutunut arvo {toteutunut_salkun_arvo:.2f} USD < "
            f"SALKUN_STOP_TASO {SALKUN_STOP_TASO} * ALKUPAAOMA {ALKUPAAOMA} = "
            f"{SALKUN_STOP_TASO * ALKUPAAOMA:.2f} USD - ei osteta"
        )
        _loki.warning(f"Ajo {ajo_id}: {perustelu}")

        for pisteytys in pisteytykset:
            tietokanta.lisaa_paatos(
                ajo_id=ajo_id,
                ajotyyppi=ajotyyppi,
                symboli=pisteytys["symboli"],
                paatos="hylatty",
                perustelu=perustelu,
                polku=polku,
            )

        yhteenveto = f"{len(pisteytykset)} paatosta, 0 ostoa (salkun stop-taso)"
        _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
        return yhteenveto

    def _jarjestysavain(pisteytys: dict):
        symboli = pisteytys["symboli"]
        ehdokas = ehdokkaat.get(symboli)
        if ehdokas is None or ehdokas.get("pisteet") is None:
            ehdokkaan_pisteet = -1
        else:
            ehdokkaan_pisteet = ehdokas["pisteet"]
        return (-pisteytys["risk_reward_pisteet"], -ehdokkaan_pisteet, symboli)

    pisteytykset_jarjestettyna = sorted(pisteytykset, key=_jarjestysavain)

    tila = {
        "aktiivisia": len(aktiiviset),
        "kateinen": kateinen,
        "aktiiviset_symbolit": {p["symboli"] for p in aktiiviset},
    }

    ostoja = 0
    hylattyja = 0
    viiteostohinta_puuttuu = 0

    for pisteytys in pisteytykset_jarjestettyna:
        symboli = pisteytys["symboli"]
        ehdokas = ehdokkaat.get(symboli)
        if ehdokas is None or ehdokas.get("viiteostohinta") is None:
            viiteostohinta_puuttuu += 1

        paatos, perustelu = paata_osto(pisteytys, ehdokas, tila)

        tietokanta.lisaa_paatos(
            ajo_id=ajo_id,
            ajotyyppi=ajotyyppi,
            symboli=symboli,
            paatos=paatos,
            perustelu=perustelu,
            polku=polku,
        )

        if paatos == "osta":
            tietokanta.avaa_positio(
                symboli=symboli,
                ostohinta=pisteytys["hinta"],
                panos_usd=POSITION_KOKO,
                osto_ajo_id=ajo_id,
                polku=polku,
            )
            tila["aktiivisia"] += 1
            tila["kateinen"] -= POSITION_KOKO
            tila["aktiiviset_symbolit"].add(symboli)
            ostoja += 1
        else:
            hylattyja += 1

    yhteenveto = (
        f"{len(pisteytykset_jarjestettyna)} paatosta, {ostoja} ostoa, "
        f"{hylattyja} hylattya, {viiteostohinta_puuttuu} ilman viiteostohintaa"
    )
    _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
    return yhteenveto
