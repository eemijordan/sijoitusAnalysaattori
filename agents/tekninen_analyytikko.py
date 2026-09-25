"""Agentti 2: hakee kurssidatan ja pisteyttää ehdokkaat riski/tuotto-suhteella."""

import json
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from core import loki, tietokanta
from core.asetukset import (
    BENCHMARK_SYMBOL,
    MIN_DAILY_TURNOVER,
    MIN_SHARE_PRICE,
    STOP_MAX_DISTANCE,
    SWING_MIN_MOVE_DAILY,
    SWING_MIN_MOVE_WEEKLY,
    get_twelve_data_key,
)

_loki = loki.get_logger(__name__)


class IncompletePriceDataError(RuntimeError):
    """Nostetaan kun uusin saatavilla oleva kurssipalkki on vielä kesken."""


# Yhteyden muodostuksen pitaa olla nopeaa - jumittunut yhteys on aito vika
# eika hitautta. Luku sen sijaan saa kestaa: time_series palauttaa 252
# palkkia, mika on satoja kilotavuja JSONia.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
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


def _get_with_retry(url: str, params: dict) -> requests.Response:
    """Tekee GET-kutsun, uusintayrittäen kerran tilapäisestä verkkovirheestä.

    Tilapäinen verkkovirhe tarkoittaa tässä aikakatkaisua tai yhteysvirhettä.
    Nämä eivät ole kiintiövirheitä kuten 429 (ks. _fetch_twelvedata_json),
    vaan hetkellisiä häiriöitä joiden uudelleenyritys todennäköisesti
    korjaa. Odottaa SYNC_SECONDS ennen uudelleenyritystä ja myös silloin
    kun toinenkin yritys epäonnistuu, koska epäonnistunutkin kutsu
    kasvattaa Twelve Datan liukuvaa krediittilaskuria - poikkeuksen ei saa
    antaa nousta suoraan ilman tätä taukoa.
    """
    try:
        return requests.get(url, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
        _loki.warning(
            f"verkkovirhe, odotetaan {SYNC_SECONDS}s ja yritetaan uudelleen: "
            f"{url} - {error}"
        )
        time.sleep(SYNC_SECONDS)
        try:
            return requests.get(
                url, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(SYNC_SECONDS)
            raise


def _fetch_twelvedata_json(url: str, params: dict) -> dict:
    """Tekee GET-kutsun ja odottaa SYNC_SECONDS sen jälkeen.

    Odottaa SYNC_SECONDS aina, myös virheen jälkeen. Jos vastaus on 429,
    odottaa LIMIT_WAIT_SECONDS ja yrittää samaa kutsua uudelleen kerran.
    Nostaa poikkeuksen jos toinenkin yritys epäonnistuu 429:ään tai jos
    HTTP-kutsu epäonnistuu muuten.
    """
    response = _get_with_retry(url, params)
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

        response = _get_with_retry(url, params)
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


def _normalize_bars_oldest_first(bars: list[dict]) -> list[dict]:
    """Muuntaa palkit muotoon {date, high, low} ja järjestää vanhimmasta uusimpaan.

    Twelve Data palauttaa palkit uusin ensin - suunta pitää näkyä koodissa
    eksplisiittisesti (bars_oldest_first), koska väärinpäin käännetty sarja
    antaisi uskottavan näköisen mutta väärän tuloksen pohjien haussa.
    """
    normalized = [
        {
            "date": bar["datetime"][:10],
            "high": float(bar["high"]),
            "low": float(bar["low"]),
        }
        for bar in bars
    ]
    normalized.sort(key=lambda bar: bar["date"])
    return normalized


def resample_weekly(bars: list[dict]) -> list[dict]:
    """Kokoaa päiväpalkeista viikkopalkit vanhimmasta uusimpaan.

    Ei haeta Twelve Datasta interval=1week - se olisi ylimääräinen API-kutsu
    per symboli, ja kuluvan viikon palkki olisi kesken samalla tavalla kuin
    kuluva päiväpalkki (ks. is_bar_ready()). Päiväpalkeista koottuna jokainen
    päivä on jo valmis. Viikko ryhmitellään ISO-viikon mukaan, jotta
    vuodenvaihteen yli menevä viikko pysyy yhtenä viikkona.
    """
    daily_bars_oldest_first = _normalize_bars_oldest_first(bars)

    weeks: dict[tuple[int, int], dict] = {}
    order: list[tuple[int, int]] = []
    for bar in daily_bars_oldest_first:
        bar_date = date.fromisoformat(bar["date"])
        iso_year, iso_week, _ = bar_date.isocalendar()
        key = (iso_year, iso_week)
        if key not in weeks:
            weeks[key] = {"date": bar_date, "high": bar["high"], "low": bar["low"]}
            order.append(key)
        else:
            week = weeks[key]
            week["date"] = min(week["date"], bar_date)
            week["high"] = max(week["high"], bar["high"])
            week["low"] = min(week["low"], bar["low"])

    return [
        {
            "date": weeks[key]["date"].isoformat(),
            "high": weeks[key]["high"],
            "low": weeks[key]["low"],
        }
        for key in order
    ]


def find_swing_lows(bars_oldest_first: list[dict], min_move: float) -> list[dict]:
    """Palauttaa vahvistetut pohjapalkit vanhimmasta uusimpaan (ZigZag).

    Pohja on vain selkeän heilahduksen pohja: kurssi on laskenut siihen
    vähintään min_move-osuuden edellisestä huipusta JA noussut siitä
    vähintään min_move-osuuden - pieni notkahdus ei kelpaa. Kääntymä
    vahvistuu vasta myöhemmällä palkilla, ei sillä palkilla joka teki uuden
    ääripisteen, joten palkki ei voi vahvistaa pohjaa omalla arvollaan (ks.
    CLAUDE.md, vaihe 2j). Sama syy tarkistetaan myös laskusuunnassa ennen
    kääntymää: palkki joka painaa pohjan uuteen, syvempään lukemaan ei voi
    vahvistaa sitä vanhaa, jo ylitettyä pohjaa vaikka sen oma yläreuna
    yltäisikin rajan yli - uusi, syvempi pohja tarvitsee oman
    vahvistuksensa myöhemmältä palkilta. Pohja kirjataan vain siirryttäessä
    laskusta nousuun - sarjan ensimmäinen kääntymä ylöspäin suoraan
    alkutilasta ei tuota pohjaa, koska sen lähtöpisteestä ei ole näyttöä
    että siihen olisi laskettu vähintään rajan verran.
    """
    if len(bars_oldest_first) < 2:
        return []

    swing_lows: list[dict] = []
    direction = None  # None, "up" tai "down"
    extreme_high = bars_oldest_first[0]
    extreme_low = bars_oldest_first[0]

    for bar in bars_oldest_first[1:]:
        if direction is None:
            if bar["low"] <= extreme_high["high"] * (1 - min_move):
                direction = "down"
                extreme_low = bar
            elif bar["high"] >= extreme_low["low"] * (1 + min_move):
                direction = "up"
                extreme_high = bar
            else:
                if bar["high"] > extreme_high["high"]:
                    extreme_high = bar
                if bar["low"] < extreme_low["low"]:
                    extreme_low = bar
        elif direction == "up":
            if bar["low"] <= extreme_high["high"] * (1 - min_move):
                direction = "down"
                extreme_low = bar
            elif bar["high"] > extreme_high["high"]:
                extreme_high = bar
        else:  # direction == "down"
            if bar["low"] < extreme_low["low"]:
                extreme_low = bar
            elif bar["high"] >= extreme_low["low"] * (1 + min_move):
                swing_lows.append(extreme_low)
                direction = "up"
                extreme_high = bar

    return swing_lows


def _select_swing_low_below_price(
    swing_lows: list[dict], current_price: float
) -> float | None:
    """Valitsee viimeisimmän vahvistetun pohjan joka on alle nykyhinnan.

    Nykyhinnan yläpuolella oleva pohja on murtunut tuki - se ohitetaan ja
    katsotaan edellistä.
    """
    for swing in reversed(swing_lows):
        if swing["low"] < current_price:
            return swing["low"]
    return None


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

    current_price = close_prices[0]
    daily_bars_oldest_first = _normalize_bars_oldest_first(bars)
    weekly_bars = resample_weekly(bars)
    swing_lows_weekly = find_swing_lows(weekly_bars, SWING_MIN_MOVE_WEEKLY)
    swing_lows_daily = find_swing_lows(daily_bars_oldest_first, SWING_MIN_MOVE_DAILY)

    return {
        "current_price": current_price,
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
        "swing_low_weekly": _select_swing_low_below_price(
            swing_lows_weekly, current_price
        ),
        "swing_low_daily": _select_swing_low_below_price(
            swing_lows_daily, current_price
        ),
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


def calculate_stop_and_target(
    price: float,
    swing_low_weekly: float | None,
    swing_low_daily: float | None,
    high_52w: float | None,
) -> tuple[float, str, float | None, float | None]:
    """Laskee stop-lossin, sen perusteen, tavoitehinnan ja RR-suhteen.

    Palauttaa (stop_loss, stop_source, target_price, rr_ratio).

    Stop asetetaan edellisen vahvistetun pohjan alle: viikkopohja ensin (jos
    enintaan STOP_MAX_DISTANCE alempana), muuten paivapohja, muuten
    STOP_MAX_DISTANCE hinnan alle (ks. CLAUDE.md, vaihe 2j). Stop on siksi
    aina maaritelty kun hinta on positiivinen - toisin kuin vanha
    ATR-pohjainen stop, joka saattoi jaada Nonesiksi.

    Tavoite on edelleen 52 viikon huippu - tarkoituksella eri lahteesta kuin
    stop, jotta RR-suhde oikeasti vaihtelee osakkeittain eika ole sama luku
    joka ikiselle osakkeelle.
    """
    weekly_distance = (
        (price - swing_low_weekly) / price if swing_low_weekly is not None else None
    )
    daily_distance = (
        (price - swing_low_daily) / price if swing_low_daily is not None else None
    )

    if weekly_distance is not None and weekly_distance <= STOP_MAX_DISTANCE:
        stop_loss = swing_low_weekly
        stop_source = "viikkopohja"
    elif daily_distance is not None and daily_distance <= STOP_MAX_DISTANCE:
        stop_loss = swing_low_daily
        stop_source = "paivapohja"
    else:
        stop_loss = price * (1 - STOP_MAX_DISTANCE)
        stop_source = "katto"

    # Tavoite jaa laskematta jos osake on jo omassa 52 viikon huipussaan -
    # talle mittarille ei silloin jaa nousuvaraa.
    target_price = None
    if high_52w is not None and high_52w > price:
        target_price = high_52w

    rr_ratio = None
    if target_price is not None:
        risk = price - stop_loss
        reward = target_price - price
        if risk > 0:
            rr_ratio = reward / risk

    return stop_loss, stop_source, target_price, rr_ratio


def _fetch_and_store_benchmark(run_id: str, db_path) -> None:
    """Hakee BENCHMARK_SYMBOL:n hinnan tarkistusajon tuottovertailua varten.

    Kutsutaan ehdokassilmukan ulkopuolella, jotta vertailukurssi tallentuu
    myos silloin kun avoimia positioita on nolla eika silmukka pyori
    kertaakaan - muuten tuottokayraan jaisi aukko niina paivina kun salkku
    on pelkkaa kateista. Haun epaonnistuminen ei saa kaataa ajoa: yksi
    puuttuva vertailukurssi ei ole syy hylata koko tarkistusajon tuloksia,
    joten salkun arvo on silti tallennettava (ks. Agentti 4).
    """
    try:
        price_data = fetch_price_data(BENCHMARK_SYMBOL)
        if price_data is None:
            _loki.warning(
                f"{BENCHMARK_SYMBOL}: time_series ei palauttanut dataa vertailulle"
            )
            return
        tietokanta.insert_market_reference(
            run_id=run_id,
            symbol=BENCHMARK_SYMBOL,
            price=price_data["current_price"],
            db_path=db_path,
        )
    except (
        IncompletePriceDataError,
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        _loki.warning(
            f"{BENCHMARK_SYMBOL}: vertailukurssin haku epaonnistui, "
            f"jatketaan ilman sita - {error}"
        )


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Hakee ajon kandidaatit, pisteyttää jokaisen ja kirjaa pisteytykset kantaan.

    Yhden symbolin virhe ei keskeytä koko ajoa, paitsi IncompletePriceDataError
    jos pörssi on auki (silloin kaikki symbolit ovat yhtä pilalla).
    """
    # Normaali- ja tarkistusajo lukevat molemmat jatkoon=1-rivit samalla
    # tavalla - Agentti 1 on jo kirjoittanut aktiiviset positiot naiksi
    # tarkistusajossa, joten sama haara riittaa.
    candidates = tietokanta.get_candidates(run_id, advanced_only=True, db_path=db_path)

    # Vertailukurssi haetaan vain tarkistusajossa - normaaliajolla ei viela
    # ole tilannekuvaa jota vasten vertailla.
    if run_type == "tarkistus":
        _fetch_and_store_benchmark(run_id, db_path)

    recorded = 0
    failed_symbols = []
    quiet_skipped = 0
    missing_stop = 0
    missing_target = 0
    first_logged = False
    for candidate in candidates:
        symbol = candidate["symbol"]
        try:
            price_data = fetch_price_data(symbol)
            if price_data is None:
                reason = "time_series ei palauttanut dataa"
                _loki.warning(f"{symbol}: {reason}, ohitetaan")
                tietokanta.insert_skip(
                    run_id=run_id,
                    symbol=symbol,
                    stage="hintadata_puuttuu",
                    reason=reason,
                    db_path=db_path,
                )
                quiet_skipped += 1
                continue

            if not first_logged:
                _loki.info(
                    f"Ajo {run_id}: viimeisin palkki ({symbol}) = "
                    f"{price_data.get('latest_bar')}"
                )
                first_logged = True

            current_price = price_data["current_price"]
            if current_price < MIN_SHARE_PRICE:
                reason = (
                    f"MIN_SHARE_PRICE-raja hylkasi (hinta {current_price} "
                    f"USD < {MIN_SHARE_PRICE} USD)"
                )
                _loki.info(f"{symbol}: {reason}, ohitetaan")
                tietokanta.insert_skip(
                    run_id=run_id,
                    symbol=symbol,
                    stage="hinta_alaraja",
                    reason=reason,
                    db_path=db_path,
                )
                quiet_skipped += 1
                continue

            turnover = price_data.get("avg_daily_turnover_usd")
            if turnover is not None and turnover < MIN_DAILY_TURNOVER:
                reason = (
                    f"MIN_DAILY_TURNOVER-raja hylkasi (paivavaihto "
                    f"{turnover:,.0f} USD < {MIN_DAILY_TURNOVER:,.0f} USD)"
                )
                _loki.info(f"{symbol}: {reason}, ohitetaan")
                tietokanta.insert_skip(
                    run_id=run_id,
                    symbol=symbol,
                    stage="vaihto_alaraja",
                    reason=reason,
                    db_path=db_path,
                )
                quiet_skipped += 1
                continue

            atr = fetch_atr(symbol)
            rsi = fetch_rsi(symbol)
        except IncompletePriceDataError:
            # Ei nielaista: jos porssi on auki, jokainen symboli on yhta
            # pilalla, joten koko ajon pitaa keskeytya eika vain ohittaa
            # yksi symboli. Orkestroija kaarii ja lokittaa taman.
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            # Vain verkko- ja datanjasennysvirheet kurssidatan haussa ja
            # jasennyksessa ohitetaan symbolikohtaisesti. calculate_score()
            # ja insert_score() ovat tarkoituksella tama try-lohkon
            # ulkopuolella: niiden virhe on ohjelmointivirhe, ei
            # verkkovirhe, eika se saa nayttaa samalta.
            _loki.warning(f"{symbol}: kasittely epaonnistui, ohitetaan - {error}")
            tietokanta.insert_skip(
                run_id=run_id,
                symbol=symbol,
                stage="haku_epaonnistui",
                reason=str(error),
                db_path=db_path,
            )
            failed_symbols.append(symbol)
            continue

        score, indicators, rationale = calculate_score(price_data, atr, rsi)
        stop_loss, stop_source, target_price, rr_ratio = calculate_stop_and_target(
            current_price,
            price_data.get("swing_low_weekly"),
            price_data.get("swing_low_daily"),
            price_data.get("high_52w"),
        )
        if stop_loss is None:
            missing_stop += 1
        if target_price is None:
            missing_target += 1

        tietokanta.insert_score(
            run_id=run_id,
            run_type=run_type,
            symbol=symbol,
            risk_reward_score=score,
            price=current_price,
            indicators_json=json.dumps(indicators),
            rationale=rationale,
            stop_loss=stop_loss,
            stop_source=stop_source,
            target_price=target_price,
            rr_ratio=rr_ratio,
            db_path=db_path,
        )
        recorded += 1

    summary = f"{recorded} pisteytysta kirjattu"
    if quiet_skipped:
        summary += f", {quiet_skipped} ohitettu"
    if failed_symbols:
        summary += f", {len(failed_symbols)} epaonnistui ({', '.join(failed_symbols)})"
        _loki.error(f"Ajo {run_id} ({run_type}): {summary}")
    else:
        _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    _loki.info(
        f"Ajo {run_id}: stop puuttui {missing_stop}, tavoite puuttui "
        f"{missing_target} kappaleelta"
    )
    return summary
