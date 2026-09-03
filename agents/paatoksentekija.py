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


def decide_buy(score: dict, candidate: dict | None, state: dict) -> tuple[str, str]:
    symbol = score.get("symbol")
    price = score.get("price")

    # 1. hinta puuttuu tai on virheellinen - ilman sita osakemaaraa ei voi
    # laskea eika ehtoa 4 tarkistaa.
    if price is None or price <= 0:
        return (
            "hylatty",
            f"hinta puuttuu tai on virheellinen ({price!r}), "
            f"ehtoa 4 ei voida tarkistaa",
        )

    # 2. duplikaatti - sama symboli ei saa olla aktiivisena kahdesti.
    if symbol in state["active_symbols"]:
        return "hylatty", f"{symbol} on jo aktiivinen positio (duplikaatti)"

    # 3. RR-kynnys.
    rr = score.get("risk_reward_score")
    if rr is None or rr < RR_THRESHOLD:
        return "hylatty", f"risk_reward_pisteet {rr} < RR_THRESHOLD {RR_THRESHOLD}"

    # 4. hinta ei saa olla karannut liian kauas viiteostohinnasta. Jos
    # viiteostohintaa ei ole, ehto ohitetaan (ei hylata) ja se nakyy
    # perustelussa - tama ei ole hiljainen huononeminen.
    reference_price = candidate.get("reference_price") if candidate else None
    price_condition_text = "viiteostohinta puuttuu, ehto 4 ohitettu"
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

    # 5. kapasiteetti.
    if state["active_count"] >= MAX_POSITIONS:
        return (
            "hylatty",
            f"aktiivisia positioita {state['active_count']} "
            f">= MAX_POSITIONS {MAX_POSITIONS}",
        )

    # 6. kateinen.
    if state["cash"] < POSITION_SIZE:
        return (
            "hylatty",
            f"kateinen {state['cash']:.2f} < POSITION_SIZE {POSITION_SIZE}",
        )

    rationale = (
        f"risk_reward_pisteet {rr} >= RR_THRESHOLD {RR_THRESHOLD}; "
        f"{price_condition_text}; "
        f"tilaa {state['active_count']}/{MAX_POSITIONS} positiota, "
        f"kateista {state['cash']:.2f} USD"
    )
    return "osta", rationale


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    if run_type != "normaali":
        message = (
            f"ajotyyppi '{run_type}': pito/myynti-logiikkaa ei ole viela toteutettu "
            f"(vaihe 3b)"
        )
        _loki.info(message)
        return message

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

    state = {
        "active_count": len(open_positions),
        "cash": cash,
        "active_symbols": {p["symbol"] for p in open_positions},
    }

    purchases = 0
    rejected = 0
    missing_reference_price = 0

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
                db_path=db_path,
            )
            state["active_count"] += 1
            state["cash"] -= POSITION_SIZE
            state["active_symbols"].add(symbol)
            purchases += 1
        else:
            rejected += 1

    summary = (
        f"{len(sorted_scores)} paatosta, {purchases} ostoa, "
        f"{rejected} hylattya, {missing_reference_price} ilman viiteostohintaa"
    )
    _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    return summary
