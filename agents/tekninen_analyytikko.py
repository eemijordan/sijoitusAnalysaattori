"""Agentti 2: hakee kurssidatan ja pisteyttää ehdokkaat riski/tuotto-suhteella."""

import json
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from core import loki, tietokanta
from core.asetukset import MIN_DAILY_TURNOVER, MIN_SHARE_PRICE, get_twelve_data_key

_loki = loki.get_logger(__name__)


class IncompletePriceDataError(RuntimeError):
    """Nostetaan kun uusin saatavilla oleva kurssipalkki on vielä kesken."""


TIMEOUT = 10
TWELVE_DATA_BASE = "https://api.twelvedata.com"

# Ilmaistaso: 8 krediittia minuutissa, liukuva laskuri jota myos
# epaonnistuneet kutsut kasvattavat. Odotetaan tama joka kutsun jalkeen
# jotta pysytaan rajan alla ilman erillista kirjanpitoa.
SYNC_SECONDS = 8
# Jos 429 tulee silti vastaan, odotetaan koko minuutti ja yritetaan samaa
# kutsua uudelleen tasan kerran.
LIMIT_WAIT_SECONDS = 60

PRICE_HISTORY_DAYS = 252
SMA50_PERIOD = 50
SMA200_PERIOD = 200


def _is_rate_limit_error(response: requests.Response, data) -> bool:
    return response.status_code == 429 or (
        isinstance(data, dict) and data.get("code") == 429
    )


def _fetch_twelvedata_json(url: str, params: dict) -> dict:
    """Tekee GET-kutsun ja odottaa SYNC_SECONDS sen jälkeen.

    Odottaa SYNC_SECONDS aina, myös virheen jälkeen. Jos vastaus on 429,
    odottaa LIMIT_WAIT_SECONDS ja yrittää samaa kutsua uudelleen kerran.
    Nostaa poikkeuksen jos toinenkin yritys epäonnistuu 429:ään tai jos
    HTTP-kutsu epäonnistuu muuten.
    """
    response = requests.get(url, params=params, timeout=TIMEOUT)
    try:
        data = response.json()
    except ValueError:
        data = None

    if _is_rate_limit_error(response, data):
        _loki.warning(
            f"Twelve Data 429 (minuuttiraja), odotetaan {LIMIT_WAIT_SECONDS}s ja "
            f"yritetaan uudelleen: {url}"
        )
        time.sleep(LIMIT_WAIT_SECONDS)

        response = requests.get(url, params=params, timeout=TIMEOUT)
        try:
            data = response.json()
        except ValueError:
            data = None
        time.sleep(SYNC_SECONDS)

        if _is_rate_limit_error(response, data):
            raise RuntimeError("Twelve Data 429-raja tuli vastaan kahdesti perakkain")

        response.raise_for_status()
        return data

    time.sleep(SYNC_SECONDS)
    response.raise_for_status()
    return data


def is_bar_ready(bar_date: date, today_et: date) -> tuple[bool, str]:
    """Kertoo onko bar_date aidosti aikaisempi kuin kuluva päivä New Yorkissa."""
    if bar_date < today_et:
        return True, ""
    reason = (
        f"viimeisin palkki {bar_date.isoformat()} on kuluva parssipaiva "
        f"(New Yorkissa {today_et.isoformat()}) - data on kesken"
    )
    return False, reason


def _calculate_price_stats(bars: list[dict]) -> dict:
    # Twelve Datan time_series palauttaa paivat uusin ensin.
    close_prices = [float(p["close"]) for p in bars]
    highs = [float(p["high"]) for p in bars]
    lows = [float(p["low"]) for p in bars]
    # Paivavaihto dollareissa = volyymi * paatoskurssi. Tama on korvike
    # markkina-arvolle, jota ei ole saatavilla ilmaistasolla.
    daily_turnovers_usd = [
        float(p["volume"]) * float(p["close"])
        for p in bars
        if p.get("volume") not in (None, "")
    ]

    sma50 = (
        sum(close_prices[:SMA50_PERIOD]) / SMA50_PERIOD
        if len(close_prices) >= SMA50_PERIOD
        else None
    )
    sma200 = (
        sum(close_prices[:SMA200_PERIOD]) / SMA200_PERIOD
        if len(close_prices) >= SMA200_PERIOD
        else None
    )

    return {
        "current_price": close_prices[0],
        "sma50": sma50,
        "sma200": sma200,
        "high_52w": max(highs),
        "low_52w": min(lows),
        "avg_daily_turnover_usd": (
            sum(daily_turnovers_usd) / len(daily_turnovers_usd)
            if daily_turnovers_usd
            else None
        ),
        "history_days": len(close_prices),
        "latest_bar": bars[0].get("datetime"),
    }


def fetch_price_data(symbol: str) -> dict | None:
    """Hakee 252 päivän kurssihistorian ja laskee siitä tunnusluvut.

    Nostaa IncompletePriceDataError:in jos uusin palkki on kesken oleva
    pörssipäivä. Palauttaa None jos Twelve Data ei palauttanut dataa lainkaan.
    """
    apikey = get_twelve_data_key()
    data = _fetch_twelvedata_json(
        f"{TWELVE_DATA_BASE}/time_series",
        {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": PRICE_HISTORY_DAYS,
            "apikey": apikey,
        },
    )

    bars = data.get("values") if isinstance(data, dict) else None
    if not isinstance(bars, list) or not bars:
        return None

    # Twelve Data palauttaa paivat uusin ensin ilman end_date-parametria.
    # Porssin ollessa auki bars[0] on kesken oleva paivan palkki: sen
    # close, high, low ja volume eivat viela ole lopullisia, ja koko
    # indikaattorisarja laskettaisiin vajaasta datasta. Siksi uusimman
    # palkin on oltava aidosti aikaisempi kuin kuluva paiva New Yorkissa.
    bar_date_str = bars[0].get("datetime")
    bar_date = None
    if isinstance(bar_date_str, str):
        try:
            bar_date = date.fromisoformat(bar_date_str[:10])
        except ValueError:
            bar_date = None

    if bar_date is None:
        raise IncompletePriceDataError(
            f"{symbol}: viimeisimman palkin datetime-kentta puuttuu tai ei "
            f"jasenny ({bar_date_str!r})"
        )

    today_et = datetime.now(ZoneInfo("America/New_York")).date()
    ready, reason = is_bar_ready(bar_date, today_et)
    if not ready:
        _loki.error(f"{symbol}: {reason}")
        raise IncompletePriceDataError(f"{symbol}: {reason}")

    return _calculate_price_stats(bars)


def _extract_indicator_value(data: dict, key: str) -> float | None:
    if not isinstance(data, dict):
        return None
    values = data.get("values")
    if not isinstance(values, list) or not values:
        return None
    try:
        return float(values[0].get(key))
    except (TypeError, ValueError):
        return None


def fetch_atr(symbol: str) -> float | None:
    """Hakee tuoreimman ATR-arvon (Average True Range)."""
    apikey = get_twelve_data_key()
    data = _fetch_twelvedata_json(
        f"{TWELVE_DATA_BASE}/atr",
        {"symbol": symbol, "interval": "1day", "outputsize": 1, "apikey": apikey},
    )
    return _extract_indicator_value(data, "atr")


def fetch_rsi(symbol: str) -> float | None:
    """Hakee tuoreimman RSI-arvon (Relative Strength Index)."""
    apikey = get_twelve_data_key()
    data = _fetch_twelvedata_json(
        f"{TWELVE_DATA_BASE}/rsi",
        {"symbol": symbol, "interval": "1day", "outputsize": 1, "apikey": apikey},
    )
    return _extract_indicator_value(data, "rsi")


def _build_rationale(
    atr_pct,
    risk_score,
    sma50,
    sma200,
    current_price,
    trend_score,
    history_days,
    position_pct,
    position_score,
    rsi,
    rsi_score,
    score,
) -> str:
    if atr_pct is not None:
        atr_text = f"ATR {atr_pct:.1f} % ({risk_score} p)".replace(".", ",")
    else:
        atr_text = f"ATR puuttuu ({risk_score} p)"

    if sma50 is None:
        trend_text = (
            f"SMA50 ei laskettavissa (vain {history_days} paivaa historiaa) (0 p)"
        )
    elif sma200 is None:
        direction = "yli SMA50" if current_price > sma50 else "alle SMA50"
        trend_text = (
            f"{direction}, SMA200 ei laskettavissa (vain {history_days} paivaa "
            f"historiaa) ({trend_score} p)"
        )
    elif current_price > sma50 and sma50 > sma200:
        trend_text = f"yli SMA50 ja SMA200 ({trend_score} p)"
    else:
        price_vs_sma50 = "yli SMA50" if current_price > sma50 else "alle SMA50"
        sma50_vs_sma200 = "yli SMA200" if sma50 > sma200 else "alle SMA200"
        trend_text = (
            f"hinta {price_vs_sma50}, SMA50 {sma50_vs_sma200} ({trend_score} p)"
        )

    if position_pct is not None:
        position_text = f"{position_pct:.0f} % vaihteluvalista ({position_score} p)"
    else:
        position_text = f"52 viikon vaihteluvali ei laskettavissa ({position_score} p)"

    if rsi is not None:
        rsi_text = f"RSI {rsi:.0f} ({rsi_score} p)"
    else:
        rsi_text = f"RSI puuttuu ({rsi_score} p)"

    score_text = f"{score:g}".replace(".", ",")
    return f"{atr_text}, {trend_text}, {position_text}, {rsi_text} = {score_text}"


def calculate_score(
    price_data: dict, atr: float | None, rsi: float | None
) -> tuple[float, dict, str]:
    """Laskee risk/reward-pisteet (0-10) riskin, trendin, aseman ja RSI:n summana.

    Palauttaa (pisteet, indikaattorit, perustelu).
    """
    current_price = price_data["current_price"]
    sma50 = price_data.get("sma50")
    sma200 = price_data.get("sma200")
    high_52w = price_data.get("high_52w")
    low_52w = price_data.get("low_52w")
    history_days = price_data.get("history_days")

    # RISKI: ATR suhteessa nykyhintaan.
    if atr is not None and current_price > 0:
        atr_pct = atr / current_price * 100
        if atr_pct < 2:
            risk_score = 3
        elif atr_pct < 4:
            risk_score = 2
        elif atr_pct < 6:
            risk_score = 1
        else:
            risk_score = 0
    else:
        atr_pct = None
        risk_score = 0

    # TRENDI: hinta > SMA50 (+2), SMA50 > SMA200 (+2). Puuttuva SMA200 -
    # yleensa alle 200 pv historiaa - jattaa vain jalkimmaisen osan pois,
    # ei olettaa sita nollaksi aanettomasti (nakyy perustelussa).
    trend_score = 0
    if sma50 is not None and current_price > sma50:
        trend_score += 2
    if sma50 is not None and sma200 is not None and sma50 > sma200:
        trend_score += 2

    # ASEMA 52 viikon vaihteluvalissa.
    if high_52w is not None and low_52w is not None and high_52w > low_52w:
        position_pct = (current_price - low_52w) / (high_52w - low_52w) * 100
        if 40 <= position_pct <= 85:
            position_score = 2
        elif position_pct > 85:
            position_score = 1  # venynyt
        else:
            position_score = 0
    else:
        position_pct = None
        position_score = 0

    # MOMENTTI (RSI).
    if rsi is not None:
        if 40 <= rsi <= 65:
            rsi_score = 1
        elif rsi > 75:
            rsi_score = -2
        elif rsi < 30:
            rsi_score = -1
        else:
            rsi_score = 0
    else:
        rsi_score = 0

    score_before_clamp = risk_score + trend_score + position_score + rsi_score
    score = max(0, min(10, score_before_clamp))

    indicators = {
        "current_price": current_price,
        "atr": atr,
        "atr_pct": atr_pct,
        "risk_score": risk_score,
        "sma50": sma50,
        "sma200": sma200,
        "trend_score": trend_score,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "position_pct": position_pct,
        "position_score": position_score,
        "rsi": rsi,
        "rsi_score": rsi_score,
        "history_days": history_days,
        "avg_daily_turnover_usd": price_data.get("avg_daily_turnover_usd"),
        "latest_bar": price_data.get("latest_bar"),
        "score_before_clamp": score_before_clamp,
    }

    rationale = _build_rationale(
        atr_pct,
        risk_score,
        sma50,
        sma200,
        current_price,
        trend_score,
        history_days,
        position_pct,
        position_score,
        rsi,
        rsi_score,
        score,
    )

    return score, indicators, rationale


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Hakee ajon kandidaatit, pisteyttää jokaisen ja kirjaa pisteytykset kantaan.

    Yhden symbolin virhe ei keskeytä koko ajoa, paitsi IncompletePriceDataError
    jos pörssi on auki (silloin kaikki symbolit ovat yhtä pilalla).
    """
    # Normaali- ja tarkistusajo lukevat molemmat jatkoon=1-rivit samalla
    # tavalla - Agentti 1 on jo kirjoittanut aktiiviset positiot naiksi
    # tarkistusajossa, joten sama haara riittaa.
    candidates = tietokanta.get_candidates(run_id, advanced_only=True, db_path=db_path)

    recorded = 0
    first_logged = False
    for candidate in candidates:
        symbol = candidate["symbol"]
        try:
            price_data = fetch_price_data(symbol)
            if price_data is None:
                _loki.warning(f"{symbol}: time_series ei palauttanut dataa, ohitetaan")
                continue

            if not first_logged:
                _loki.info(
                    f"Ajo {run_id}: viimeisin palkki ({symbol}) = "
                    f"{price_data.get('latest_bar')}"
                )
                first_logged = True

            current_price = price_data["current_price"]
            if current_price < MIN_SHARE_PRICE:
                _loki.info(
                    f"{symbol}: MIN_SHARE_PRICE-raja hylkasi (hinta {current_price} "
                    f"USD < {MIN_SHARE_PRICE} USD), ohitetaan"
                )
                continue

            turnover = price_data.get("avg_daily_turnover_usd")
            if turnover is not None and turnover < MIN_DAILY_TURNOVER:
                _loki.info(
                    f"{symbol}: MIN_DAILY_TURNOVER-raja hylkasi (paivavaihto "
                    f"{turnover:,.0f} USD < {MIN_DAILY_TURNOVER:,.0f} USD), "
                    f"ohitetaan"
                )
                continue

            atr = fetch_atr(symbol)
            rsi = fetch_rsi(symbol)

            score, indicators, rationale = calculate_score(price_data, atr, rsi)

            tietokanta.insert_score(
                run_id=run_id,
                run_type=run_type,
                symbol=symbol,
                risk_reward_score=score,
                price=current_price,
                indicators_json=json.dumps(indicators),
                rationale=rationale,
                db_path=db_path,
            )
            recorded += 1
        except IncompletePriceDataError:
            # Ei nielaista: jos porssi on auki, jokainen symboli on yhta
            # pilalla, joten koko ajon pitaa keskeytya eika vain ohittaa
            # yksi symboli. Orkestroija kaarii ja lokittaa taman.
            raise
        except Exception as error:
            _loki.warning(f"{symbol}: kasittely epaonnistui, ohitetaan - {error}")
            continue

    summary = f"{recorded} pisteytysta kirjattu"
    _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    return summary
