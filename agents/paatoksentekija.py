from core import loki, tietokanta
from core.asetukset import (
    INITIAL_CAPITAL,
    MAX_POSITIONS,
    MAX_PRICE_DRIFT,
    PORTFOLIO_STOP_LEVEL,
    POSITION_SIZE,
    RR_THRESHOLD,
)

_loki = loki.get_logger(__name__)


def paata_osto(pisteytys: dict, ehdokas: dict | None, tila: dict) -> tuple[str, str]:
    symboli = pisteytys.get("symbol")
    hinta = pisteytys.get("price")

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
    rr = pisteytys.get("risk_reward_score")
    if rr is None or rr < RR_THRESHOLD:
        return "hylatty", f"risk_reward_pisteet {rr} < RR_THRESHOLD {RR_THRESHOLD}"

    # 4. hinta ei saa olla karannut liian kauas viiteostohinnasta. Jos
    # viiteostohintaa ei ole, ehto ohitetaan (ei hylata) ja se nakyy
    # perustelussa - tama ei ole hiljainen huononeminen.
    viiteostohinta = ehdokas.get("reference_price") if ehdokas else None
    hintaehto_teksti = "viiteostohinta puuttuu, ehto 4 ohitettu"
    if viiteostohinta is not None:
        poikkeama = (hinta - viiteostohinta) / viiteostohinta
        poikkeama_pct = poikkeama * 100
        raja_pct = MAX_PRICE_DRIFT * 100
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
        if abs(poikkeama) > MAX_PRICE_DRIFT:
            return "hylatty", hintaehto_teksti

    # 5. kapasiteetti.
    if tila["aktiivisia"] >= MAX_POSITIONS:
        return (
            "hylatty",
            f"aktiivisia positioita {tila['aktiivisia']} "
            f">= MAX_POSITIONS {MAX_POSITIONS}",
        )

    # 6. kateinen.
    if tila["kateinen"] < POSITION_SIZE:
        return (
            "hylatty",
            f"kateinen {tila['kateinen']:.2f} < POSITION_SIZE {POSITION_SIZE}",
        )

    perustelu = (
        f"risk_reward_pisteet {rr} >= RR_THRESHOLD {RR_THRESHOLD}; {hintaehto_teksti}; "
        f"tilaa {tila['aktiivisia']}/{MAX_POSITIONS} positiota, "
        f"kateista {tila['kateinen']:.2f} USD"
    )
    return "osta", perustelu


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.DEFAULT_DB_PATH) -> str:
    if ajotyyppi != "normaali":
        viesti = (
            f"ajotyyppi '{ajotyyppi}': pito/myynti-logiikkaa ei ole viela toteutettu "
            f"(vaihe 3b)"
        )
        _loki.info(viesti)
        return viesti

    pisteytykset = tietokanta.get_scores(ajo_id, db_path=polku)
    if not pisteytykset:
        yhteenveto = "0 pisteytysta, 0 paatosta"
        _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
        return yhteenveto

    ehdokasrivit = tietokanta.get_candidates(ajo_id, advanced_only=False, db_path=polku)
    ehdokkaat: dict = {}
    for rivi in ehdokasrivit:
        if rivi["symbol"] not in ehdokkaat:
            ehdokkaat[rivi["symbol"]] = rivi

    aktiiviset = tietokanta.get_open_positions(db_path=polku)
    kateinen = tietokanta.get_cash(db_path=polku)

    # Salkkutason pysaytin ennen silmukkaa. Huom: mittari huomioi vain
    # toteutuneet tappiot (kateinen + avointen positioiden panokset), koska
    # avointen positioiden nykyhintoja ei ole saatavilla ilman API-kutsuja.
    toteutunut_salkun_arvo = kateinen + sum(p["stake_usd"] for p in aktiiviset)
    if toteutunut_salkun_arvo < PORTFOLIO_STOP_LEVEL * INITIAL_CAPITAL:
        perustelu = (
            f"salkkutason pysaytin: toteutunut arvo {toteutunut_salkun_arvo:.2f} USD < "
            f"PORTFOLIO_STOP_LEVEL {PORTFOLIO_STOP_LEVEL} * INITIAL_CAPITAL "
            f"{INITIAL_CAPITAL} = "
            f"{PORTFOLIO_STOP_LEVEL * INITIAL_CAPITAL:.2f} USD - ei osteta"
        )
        _loki.warning(f"Ajo {ajo_id}: {perustelu}")

        for pisteytys in pisteytykset:
            tietokanta.insert_decision(
                run_id=ajo_id,
                run_type=ajotyyppi,
                symbol=pisteytys["symbol"],
                decision="hylatty",
                rationale=perustelu,
                db_path=polku,
            )

        yhteenveto = f"{len(pisteytykset)} paatosta, 0 ostoa (salkun stop-taso)"
        _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
        return yhteenveto

    def _jarjestysavain(pisteytys: dict):
        symboli = pisteytys["symbol"]
        ehdokas = ehdokkaat.get(symboli)
        if ehdokas is None or ehdokas.get("score") is None:
            ehdokkaan_pisteet = -1
        else:
            ehdokkaan_pisteet = ehdokas["score"]
        return (-pisteytys["risk_reward_score"], -ehdokkaan_pisteet, symboli)

    pisteytykset_jarjestettyna = sorted(pisteytykset, key=_jarjestysavain)

    tila = {
        "aktiivisia": len(aktiiviset),
        "kateinen": kateinen,
        "aktiiviset_symbolit": {p["symbol"] for p in aktiiviset},
    }

    ostoja = 0
    hylattyja = 0
    viiteostohinta_puuttuu = 0

    for pisteytys in pisteytykset_jarjestettyna:
        symboli = pisteytys["symbol"]
        ehdokas = ehdokkaat.get(symboli)
        if ehdokas is None or ehdokas.get("reference_price") is None:
            viiteostohinta_puuttuu += 1

        paatos, perustelu = paata_osto(pisteytys, ehdokas, tila)

        tietokanta.insert_decision(
            run_id=ajo_id,
            run_type=ajotyyppi,
            symbol=symboli,
            decision=paatos,
            rationale=perustelu,
            db_path=polku,
        )

        if paatos == "osta":
            tietokanta.open_position(
                symbol=symboli,
                buy_price=pisteytys["price"],
                stake_usd=POSITION_SIZE,
                buy_run_id=ajo_id,
                db_path=polku,
            )
            tila["aktiivisia"] += 1
            tila["kateinen"] -= POSITION_SIZE
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
