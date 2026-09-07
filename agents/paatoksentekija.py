"""Agentti 3: päättää kunkin pisteytyksen perusteella osto, pito vai myynti."""

from datetime import date

from core import loki, tietokanta
from core.asetukset import (
    EXIT_SCORE_THRESHOLD,
    INITIAL_CAPITAL,
    MAX_BUYS_PER_RUN,
    MAX_POSITIONS,
    MAX_PRICE_DRIFT,
    PORTFOLIO_STOP_LEVEL,
    POSITION_SIZE,
    REPURCHASE_COOLDOWN_DAYS,
    RR_THRESHOLD,
)

_loki = loki.get_logger(__name__)


def decide_buy(score: dict, candidate: dict | None, state: dict) -> tuple[str, str]:
    """Soveltaa 8 ostoehtoa järjestyksessä ja palauttaa (päätös, perustelu).

    Päätös on 'osta' tai 'hylatty'. Ensimmäinen läpäisemätön ehto ratkaisee.
    Ehdot jakautuvat kolmeen kerrokseen: kelpoisuus (saako tätä symbolia edes
    harkita), laatu (onko tämä hyvä ehdokas) ja kapasiteetti (mahtuuko tämä
    salkkuun juuri nyt). Jäähy on kelpoisuuskerroksessa duplikaatin rinnalla:
    symbolia ei saa harkita olipa se kuinka hyvä tahansa. MAX_BUYS_PER_RUN on
    sen sijaan kapasiteettikerroksen viimeinen ehto - kun se laukeaa, tiedät
    että ehdokas olisi muuten ostettu, ja juuri se tieto kertoo onko raja
    oikealla tasolla.
    """
    symbol = score.get("symbol")
    price = score.get("price")

    # --- Kelpoisuus: saako tätä symbolia edes harkita? ---

    # 1. hinta puuttuu tai on virheellinen - ilman sita osakemaaraa ei voi
    # laskea eika ehtoa 5 tarkistaa.
    if price is None or price <= 0:
        return (
            "hylatty",
            f"hinta puuttuu tai on virheellinen ({price!r}), "
            f"ehtoa 5 ei voida tarkistaa",
        )

    # 2. duplikaatti - sama symboli ei saa olla aktiivisena kahdesti.
    if symbol in state["active_symbols"]:
        return "hylatty", f"{symbol} on jo aktiivinen positio (duplikaatti)"

    # 3. jaahy - stopattu tai muuten suljettu symboli ei saa palata heti,
    # koska sama vedon asettaminen uudelleen maksaisi saman tappion
    # toistamiseen (ks. REPURCHASE_COOLDOWN_DAYS).
    cooldown_until = state["cooldown_until"].get(symbol)
    if cooldown_until is not None:
        return (
            "hylatty",
            f"{symbol} on jaahylla {cooldown_until} asti "
            f"(REPURCHASE_COOLDOWN_DAYS {REPURCHASE_COOLDOWN_DAYS})",
        )

    # --- Laatu: onko tämä hyvä ehdokas? ---

    # 4. RR-kynnys.
    rr = score.get("risk_reward_score")
    if rr is None or rr < RR_THRESHOLD:
        return "hylatty", f"risk_reward_pisteet {rr} < RR_THRESHOLD {RR_THRESHOLD}"

    # 5. hinta ei saa olla karannut liian kauas viiteostohinnasta. Jos
    # viiteostohintaa ei ole, ehto ohitetaan (ei hylata) ja se nakyy
    # perustelussa - tama ei ole hiljainen huononeminen.
    reference_price = candidate.get("reference_price") if candidate else None
    price_condition_text = "viiteostohinta puuttuu, ehto 5 ohitettu"
    if reference_price is not None:
        deviation = (price - reference_price) / reference_price
        deviation_pct = deviation * 100
        limit_pct = MAX_PRICE_DRIFT * 100
        if deviation > 0:
            price_condition_text = (
                f"hinta {price:.2f} on {deviation_pct:.1f} % yli viiteostohinnan "
                f"{reference_price:.2f} (raja {limit_pct:.1f} %)"
            )
        else:
            price_condition_text = (
                f"hinta {price:.2f} on {abs(deviation_pct):.1f} % alle "
                f"viiteostohinnan {reference_price:.2f} (raja {limit_pct:.1f} %)"
            )
        if abs(deviation) > MAX_PRICE_DRIFT:
            return "hylatty", price_condition_text

    # --- Kapasiteetti: mahtuuko tämä salkkuun juuri nyt? ---

    # 6. kapasiteetti.
    if state["active_count"] >= MAX_POSITIONS:
        return (
            "hylatty",
            f"aktiivisia positioita {state['active_count']} "
            f">= MAX_POSITIONS {MAX_POSITIONS}",
        )

    # 7. kateinen.
    if state["cash"] < POSITION_SIZE:
        return (
            "hylatty",
            f"kateinen {state['cash']:.2f} < POSITION_SIZE {POSITION_SIZE}",
        )

    # 8. ostoraja per ajo - hajauttaa sisaanmenot ajassa (ks.
    # MAX_BUYS_PER_RUN). Vain onnistunut osto kuluttaa kiintiota, joten tama
    # ehto tarkistetaan viimeisena: se kertoo ainoastaan siita etteivat
    # kovat rajat enaa estaneet ostoa.
    if state["buys_this_run"] >= MAX_BUYS_PER_RUN:
        return (
            "hylatty",
            f"MAX_BUYS_PER_RUN {MAX_BUYS_PER_RUN} taynna tassa ajossa",
        )

    rationale = (
        f"risk_reward_pisteet {rr} >= RR_THRESHOLD {RR_THRESHOLD}; "
        f"{price_condition_text}; "
        f"tilaa {state['active_count']}/{MAX_POSITIONS} positiota, "
        f"kateista {state['cash']:.2f} USD"
    )
    return "osta", rationale


def decide_hold_or_sell(position: dict, score: dict | None) -> tuple[str, str]:
    """Soveltaa 5 pito/myynti-ehtoa järjestyksessä ja palauttaa (päätös, perustelu).

    Päätös on 'pida' tai 'myy'. Ensimmäinen täyttyvä ehto ratkaisee. Stop ja
    tavoite tarkistetaan ennen pisteitä - jos kurssi on rikkonut stopin,
    sillä ei ole väliä miltä pisteet näyttävät, riskiraja on riskiraja.
    """
    price = score.get("price") if score else None

    # 1. tuoretta hintaa ei ole - ei koskaan myyda puuttuvan datan
    # perusteella. Vaiheen 2f verkkovirheet osoittivat etta symboli voi
    # kadota ajosta ilman etta mikaan on vialla itse positiossa.
    if score is None:
        return (
            "pida",
            "pisteytysta ei ole talta ajolta, ei myyda puuttuvan datan perusteella",
        )
    if price is None or price <= 0:
        return (
            "pida",
            f"hinta puuttuu tai on virheellinen ({price!r}), "
            f"ei myyda puuttuvan datan perusteella",
        )

    # 2. stop-loss.
    stop_loss = position.get("stop_loss")
    if stop_loss is not None and price <= stop_loss:
        return "myy", f"stop-loss osui: hinta {price:.2f} <= stop {stop_loss:.2f}"

    # 3. tavoitehinta.
    target_price = position.get("target_price")
    if target_price is not None and price >= target_price:
        return (
            "myy",
            f"tavoite saavutettu: hinta {price:.2f} >= tavoite {target_price:.2f}",
        )

    # 4. pisteet pudonneet exit-kynnyksen alle (hystereesi, ks. asetukset.py).
    rr = score.get("risk_reward_score")
    if rr is not None and rr < EXIT_SCORE_THRESHOLD:
        return "myy", f"pisteet {rr} < EXIT_SCORE_THRESHOLD {EXIT_SCORE_THRESHOLD}"

    return (
        "pida",
        f"pisteet {rr} >= EXIT_SCORE_THRESHOLD {EXIT_SCORE_THRESHOLD}, "
        f"stop ja tavoite eivat osuneet",
    )


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Ajaa osto- tai tarkistuslogiikan run_type:n mukaan."""
    if run_type == "tarkistus":
        return _run_review(run_id, run_type, db_path)
    return _run_normal(run_id, run_type, db_path)


def _run_review(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Tarkistaa jokaisen avoimen position stopin, tavoitteen ja pisteet.

    Jokaisesta positiosta syntyy tasan yksi päätösrivi ('pida' tai 'myy')
    riippumatta siitä löytyykö sille tuoretta pisteytystä tältä ajolta.
    Päätös kirjataan aina ensin, ennen kuin positions- tai portfolio-taulua
    kosketaan (sääntö "ei sijoituspäätöstä ilman lokia").

    **Tunnettu rajoitus:** stop ja tavoite tarkistetaan vain päätöskurssia
    vastaan (technical_scores.price on Agentti 2:n viimeisimmän palkin
    päätöskurssi, ei sen päivän ylin/alin). Jos osake romahtaa stopin läpi
    kesken viikon ja toipuu tarkistuspäivään mennessä, tämä ajo näkee vain
    lievästi tappiollisen päätöskurssin ja päättää pitää - tappioita
    aliarvioidaan järjestelmällisesti. Korjauspolku (tallentaa jakson ylin/
    alin Agentti 2:ssa) on tiedossa mutta kuuluu omaan vaiheeseensa.
    """
    positions = tietokanta.get_open_positions(db_path=db_path)
    if not positions:
        summary = "0 aktiivista positiota, 0 paatosta"
        _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
        return summary

    scores_by_symbol = {
        score["symbol"]: score
        for score in tietokanta.get_scores(run_id, db_path=db_path)
    }

    sold = 0
    held = 0
    total_pnl = 0.0

    for position in positions:
        symbol = position["symbol"]
        score = scores_by_symbol.get(symbol)
        decision, rationale = decide_hold_or_sell(position, score)

        tietokanta.insert_decision(
            run_id=run_id,
            run_type=run_type,
            symbol=symbol,
            decision=decision,
            rationale=rationale,
            db_path=db_path,
        )

        if decision == "myy":
            closed = tietokanta.close_position(
                symbol=symbol,
                sell_price=score["price"],
                sell_run_id=run_id,
                db_path=db_path,
            )
            total_pnl += closed["pnl_usd"]
            sold += 1
        else:
            tietokanta.update_last_checked(symbol, db_path=db_path)
            held += 1

    summary = f"{len(positions)} positiota tarkistettu, {sold} myytya, {held} pidettya"
    _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    if sold:
        _loki.info(
            f"Ajo {run_id}: myytyjen yhteenlaskettu voitto/tappio {total_pnl:.2f} USD"
        )
    return summary


def _run_normal(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Pisteyttää ajon kandidaatit, tarkistaa salkun stop-tason ja tekee ostot.

    Käsittelee pisteytykset RR-järjestyksessä parhaasta huonoimpaan. Jokaisesta
    pisteytyksestä syntyy tasan yksi päätösrivi riippumatta lopputuloksesta.
    """
    scores = tietokanta.get_scores(run_id, db_path=db_path)
    if not scores:
        summary = "0 pisteytysta, 0 paatosta"
        _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
        return summary

    candidate_rows = tietokanta.get_candidates(
        run_id, advanced_only=False, db_path=db_path
    )
    candidates: dict = {}
    for row in candidate_rows:
        if row["symbol"] not in candidates:
            candidates[row["symbol"]] = row

    open_positions = tietokanta.get_open_positions(db_path=db_path)
    cash = tietokanta.get_cash(db_path=db_path)

    # Salkkutason pysaytin ennen silmukkaa. Huom: mittari huomioi vain
    # toteutuneet tappiot (kateinen + avointen positioiden panokset), koska
    # avointen positioiden nykyhintoja ei ole saatavilla ilman API-kutsuja.
    realized_portfolio_value = cash + sum(p["stake_usd"] for p in open_positions)
    if realized_portfolio_value < PORTFOLIO_STOP_LEVEL * INITIAL_CAPITAL:
        rationale = (
            f"salkkutason pysaytin: toteutunut arvo "
            f"{realized_portfolio_value:.2f} USD < "
            f"PORTFOLIO_STOP_LEVEL {PORTFOLIO_STOP_LEVEL} * INITIAL_CAPITAL "
            f"{INITIAL_CAPITAL} = "
            f"{PORTFOLIO_STOP_LEVEL * INITIAL_CAPITAL:.2f} USD - ei osteta"
        )
        _loki.warning(f"Ajo {run_id}: {rationale}")

        for score in scores:
            tietokanta.insert_decision(
                run_id=run_id,
                run_type=run_type,
                symbol=score["symbol"],
                decision="hylatty",
                rationale=rationale,
                db_path=db_path,
            )

        summary = f"{len(scores)} paatosta, 0 ostoa (salkun stop-taso)"
        _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
        return summary

    def _sort_key(score: dict):
        symbol = score["symbol"]
        candidate = candidates.get(symbol)
        if candidate is None or candidate.get("score") is None:
            candidate_score = -1
        else:
            candidate_score = candidate["score"]
        return (-score["risk_reward_score"], -candidate_score, symbol)

    sorted_scores = sorted(scores, key=_sort_key)

    cooldown_until = tietokanta.get_cooldown_symbols(
        REPURCHASE_COOLDOWN_DAYS, date.today(), db_path=db_path
    )

    state = {
        "active_count": len(open_positions),
        "cash": cash,
        "active_symbols": {p["symbol"] for p in open_positions},
        "buys_this_run": 0,
        "cooldown_until": cooldown_until,
    }

    purchases = 0
    rejected = 0
    missing_reference_price = 0
    buy_limit_hit = False

    for score in sorted_scores:
        symbol = score["symbol"]
        candidate = candidates.get(symbol)
        if candidate is None or candidate.get("reference_price") is None:
            missing_reference_price += 1

        decision, rationale = decide_buy(score, candidate, state)

        tietokanta.insert_decision(
            run_id=run_id,
            run_type=run_type,
            symbol=symbol,
            decision=decision,
            rationale=rationale,
            db_path=db_path,
        )

        if decision == "osta":
            tietokanta.open_position(
                symbol=symbol,
                buy_price=score["price"],
                stake_usd=POSITION_SIZE,
                buy_run_id=run_id,
                stop_loss=score["stop_loss"],
                target_price=score["target_price"],
                db_path=db_path,
            )
            state["active_count"] += 1
            state["cash"] -= POSITION_SIZE
            state["active_symbols"].add(symbol)
            state["buys_this_run"] += 1
            purchases += 1
        else:
            if "MAX_BUYS_PER_RUN" in rationale:
                buy_limit_hit = True
            rejected += 1

    summary = (
        f"{len(sorted_scores)} paatosta, {purchases} ostoa, "
        f"{rejected} hylattya, {missing_reference_price} ilman viiteostohintaa"
    )
    if buy_limit_hit:
        summary += f", MAX_BUYS_PER_RUN {MAX_BUYS_PER_RUN} esti lisaostoja"
    _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    return summary
