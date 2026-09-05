"""Agentti 1: hakee neljä signaalilähdettä ja pisteyttää niistä ehdokkaat."""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from core import loki, tietokanta
from core.asetukset import (
    CANDIDATE_TARGET,
    CANDIDATE_THRESHOLD,
    CO_OCCURRENCE_THRESHOLD,
    MAX_SELECTED,
    MIN_SHARE_PRICE,
    get_fmp_key,
    get_twelve_data_key,
)

_loki = loki.get_logger(__name__)

TIMEOUT = 10
FMP_BASE = "https://financialmodelingprep.com/stable"
TWELVE_DATA_STOCKS_URL = "https://api.twelvedata.com/stocks"
VALIMUISTI_DIR = tietokanta.DATA_DIR / "valimuisti"
# Kirjoita-kerran-arkisto (ks. CLAUDE.md) - toisin kuin valimuisti, tata ei
# koskaan siivota eika luetakaan taalta, vain kirjoitetaan. Sama polku jota
# tietokanta.py kayttaa runs-metatiedostolle (_ajo.json) - yksi totuuden lahde.
ARKISTO_DIR = tietokanta.ARKISTO_DIR

PAGE_SIZE = 10
# Talla FMP-tasolla vain page=0 on sallittu (katto on tilikohtainen, todettu
# koettimella - page=1 -> 402 kaikilla kolmella paatepisteella). Jos taso
# joskus paivittyy, riittaa kasvattaa tata lukua.
MAX_PAGES = 1

SYMBOL_REGEX = re.compile(r"^[A-Z]{1,5}$")
ACCEPTED_RATINGS = {"buy", "strong buy", "outperform", "overweight"}
AMOUNT_NUMBER_REGEX = re.compile(r"[\d][\d,]*")
MAX_ANALYST_AGE_DAYS = 14
MIN_US_SYMBOL_COUNT = 1000

# Twelve Datan /stocks-vastauksessa (country=United States) esiintyvat
# exchange-arvot: NASDAQ, NYSE, CBOE, IEX, OTC, XSTU (viimeinen on itse
# asiassa Stuttgartin porssi). Vain kaksi ensimmaista ovat US-paaporssit;
# loput hylataan (OTC nimenomaisesti pyydetysti, muut alaporssit/ulkomaiset
# samalla perusteella).
ACCEPTED_EXCHANGES = {"NASDAQ", "NYSE"}

# Koko maan /stocks-haku (country=United States, ei exchange-suodatinta)
# palauttaa yli 20 000 riviaa ja on megatavuja - eri suuruusluokkaa kuin
# FMP-haut, joten sille tarvitaan oma, pidempi timeout.
SYMBOL_LIST_TIMEOUT = 60
# Porssien symbolilista ei muutu paivittain (uusia listautumisia muutama
# viikossa), joten sille riittaa 30 vrk:n valimuisti - raskas haku tehdaan
# noin kerran kuukaudessa eika joka kylmalla ajolla.
SYMBOL_LIST_CACHE_DAYS = 30
SYMBOL_LIST_FILENAME = "twelvedata-stocks"
# Jos yhden porssin exchange-suodatettu vastaus on nain iso, exchange-
# parametri on todennakoisesti jaanyt FMP:n/Twelve Datan paassa huomiotta
# (koko maan vastaus on ~20 000 riviaa, yksittaisen paaporssin pitaisi olla
# muutama tuhat).
SUSPICIOUSLY_LARGE_EXCHANGE_RESPONSE = 10000

# Karkea jarjestysasteikko "korotus vs. laskenus" -paattelyyn silloin kun
# grades-latest-news-rivilta puuttuu oma action-kentta.
RATING_ORDER = {
    "strong sell": 0,
    "sell": 1,
    "underweight": 1,
    "underperform": 1,
    "reduce": 1,
    "hold": 2,
    "neutral": 2,
    "market perform": 2,
    "sector perform": 2,
    "equal weight": 2,
    "overweight": 3,
    "outperform": 3,
    "buy": 4,
    "accumulate": 4,
    "strong buy": 5,
}


def _archive_response(run_id: str | None, name: str, data) -> None:
    """Kirjoittaa raa'an vastauksen data/arkisto/<run_id>/<name>.json:iin.

    Ohitetaan jos run_id on None (esim. tools/api_koetin.py tai testit,
    joilla ei ole ajoa jonka alle arkistoida). Arkistoa ei koskaan lueta
    täältä - se on kirjoitusta varten, välimuisti hoitaa lukemisen.
    """
    if run_id is None:
        return
    archive_dir = ARKISTO_DIR / run_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    file_path = archive_dir / f"{name}.json"
    with open(file_path, "w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle)


def _fetch_json_cached(name: str, url: str, params: dict, run_id: str | None = None):
    """Hakee JSON:in päivän mittaisella tiedostovälimuistilla.

    Kirjoittaa lisäksi raa'an vastauksen arkistoon (ks. _archive_response)
    jos run_id on annettu - myös silloin kun välimuisti palvelee kutsun,
    koska kahden saman päivän ajon pitää saada omat arkistokopionsa.
    """
    VALIMUISTI_DIR.mkdir(parents=True, exist_ok=True)
    file_path = VALIMUISTI_DIR / f"{name}_{date.today().isoformat()}.json"

    if file_path.exists():
        with open(file_path, encoding="utf-8") as file_handle:
            data = json.load(file_handle)
        _archive_response(run_id, name, data)
        return data

    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()

    with open(file_path, "w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle)

    _archive_response(run_id, name, data)
    return data


def _fetch_paginated(
    name: str, url: str, apikey: str, run_id: str | None = None
) -> list[dict]:
    """Hakee sivutetun FMP-päätepisteen, pysähtyen tyhjään tai toistuvaan sivuun."""
    all_rows: list[dict] = []
    first_page = None

    for page in range(MAX_PAGES):
        try:
            data = _fetch_json_cached(
                f"{name}_sivu{page}",
                url,
                {"page": page, "limit": PAGE_SIZE, "apikey": apikey},
                run_id=run_id,
            )
        except requests.RequestException:
            if page == 0:
                raise
            _loki.warning(
                f"{name}: sivu {page} epaonnistui, kaytetaan tahan mennessa saatuja "
                f"{len(all_rows)} rivia"
            )
            break

        if not isinstance(data, list):
            if page == 0:
                raise RuntimeError(f"{name} palautti odottamattoman muodon: {data!r}")
            _loki.warning(
                f"{name}: sivu {page} palautti odottamattoman muodon, "
                f"lopetetaan sivutus"
            )
            break

        if not data:
            break

        if page == 0:
            first_page = data
        elif data == first_page:
            _loki.warning(
                f"{name}: sivu {page} palautti samat rivit kuin sivu 0 - API ei nayta "
                f"tukevan sivutusta, lopetetaan"
            )
            break

        all_rows.extend(data)

    _loki.info(f"{name}: haettu {len(all_rows)} rivia")
    return all_rows


def fetch_analyst_changes(run_id: str | None = None) -> list[dict]:
    """Hakee FMP:n tuoreimmat analyytikkoluokitusmuutokset."""
    apikey = get_fmp_key()
    return _fetch_paginated(
        "grades-latest-news", f"{FMP_BASE}/grades-latest-news", apikey, run_id=run_id
    )


def fetch_congress_trades(run_id: str | None = None) -> list[dict]:
    """Hakee FMP:n tuoreimmat senaatin ja edustajainhuoneen kaupat."""
    apikey = get_fmp_key()
    senate = _fetch_paginated(
        "senate-latest", f"{FMP_BASE}/senate-latest", apikey, run_id=run_id
    )
    house = _fetch_paginated(
        "house-latest", f"{FMP_BASE}/house-latest", apikey, run_id=run_id
    )
    return senate + house


def fetch_insider_trades(run_id: str | None = None) -> list[dict]:
    """Hakee FMP:n tuoreimmat sisäpiirikaupat."""
    apikey = get_fmp_key()
    return _fetch_paginated(
        "insider-trading-latest",
        f"{FMP_BASE}/insider-trading/latest",
        apikey,
        run_id=run_id,
    )


def fetch_price_targets(run_id: str | None = None) -> list[dict]:
    """Hakee FMP:n tuoreimmat analyytikkojen tavoitehinnat."""
    apikey = get_fmp_key()
    return _fetch_paginated(
        "price-target-latest-news",
        f"{FMP_BASE}/price-target-latest-news",
        apikey,
        run_id=run_id,
    )


def _cached_symbol_list_files() -> list[Path]:
    if not VALIMUISTI_DIR.exists():
        return []
    return sorted(VALIMUISTI_DIR.glob(f"{SYMBOL_LIST_FILENAME}_*.json"), reverse=True)


def _fetch_cached_symbol_list(max_age_days: int) -> tuple[set[str], int] | None:
    """Palauttaa uusimman korkeintaan max_age_days ikäisen välimuistin."""
    today = date.today()
    for file_path in _cached_symbol_list_files():
        date_str = file_path.stem.removeprefix(f"{SYMBOL_LIST_FILENAME}_")
        file_date = _parse_date(date_str)
        if file_date is None:
            continue

        age = (today - file_date).days
        if age > max_age_days:
            continue

        try:
            with open(file_path, encoding="utf-8") as file_handle:
                symbol_list = json.load(file_handle)
        except (OSError, ValueError):
            continue

        if not isinstance(symbol_list, list):
            continue

        return set(symbol_list), age

    return None


def _save_symbol_list_to_cache(symbols: set[str]) -> None:
    VALIMUISTI_DIR.mkdir(parents=True, exist_ok=True)
    file_path = (
        VALIMUISTI_DIR / f"{SYMBOL_LIST_FILENAME}_{date.today().isoformat()}.json"
    )
    with open(file_path, "w", encoding="utf-8") as file_handle:
        json.dump(sorted(symbols), file_handle)


def _fetch_exchange_symbols(exchange: str, apikey: str) -> list[dict]:
    response = requests.get(
        TWELVE_DATA_STOCKS_URL,
        params={"country": "United States", "exchange": exchange, "apikey": apikey},
        timeout=SYMBOL_LIST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(
            f"Twelve Data /stocks (exchange={exchange}) palautti odottamattoman "
            f"muodon: {data!r}"
        )
    return rows


def _fetch_all_country_symbols(apikey: str) -> list[dict]:
    response = requests.get(
        TWELVE_DATA_STOCKS_URL,
        params={"country": "United States", "apikey": apikey},
        timeout=SYMBOL_LIST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RuntimeError(
            f"Twelve Data /stocks palautti odottamattoman muodon: {data!r}"
        )
    return data["data"]


def _fetch_symbol_list_from_network(apikey: str) -> set[str]:
    """Hakee US-symbolit pörsseittäin, tai koko maan listalta jos suodatin ei toimi."""
    by_exchange: dict[str, list[dict]] = {}
    param_works = True

    for exchange in sorted(ACCEPTED_EXCHANGES):
        rows = _fetch_exchange_symbols(exchange, apikey)
        if len(rows) > SUSPICIOUSLY_LARGE_EXCHANGE_RESPONSE:
            _loki.warning(
                f"Twelve Data /stocks?exchange={exchange} palautti {len(rows)} rivia - "
                f"exchange-parametri nayttaa jaaneen huomiotta, palataan "
                f"suodattamattomaan hakuun"
            )
            param_works = False
            break
        by_exchange[exchange] = rows

    if param_works:
        symbols = {
            str(row.get("symbol", "")).strip()
            for rows in by_exchange.values()
            for row in rows
            if isinstance(row, dict)
        }
        symbols.discard("")
        return symbols

    all_rows = _fetch_all_country_symbols(apikey)
    symbols = {
        str(row.get("symbol", "")).strip()
        for row in all_rows
        if isinstance(row, dict) and row.get("exchange") in ACCEPTED_EXCHANGES
    }
    symbols.discard("")
    return symbols


def fetch_us_symbols() -> set[str]:
    """Hakee hyväksyttyjen US-pörssien (NASDAQ, NYSE) symbolit, 30 vrk välimuistilla.

    Nostaa RuntimeErrorin jos haku epäonnistuu eikä kelvollista välimuistia ole.
    """
    apikey = get_twelve_data_key()

    cached = _fetch_cached_symbol_list(SYMBOL_LIST_CACHE_DAYS)
    if cached is not None:
        symbols, age = cached
        _loki.info(
            f"twelvedata-stocks: kaytetaan {age} vrk vanhaa valimuistia, "
            f"{len(symbols)} symbolia"
        )
        return symbols

    try:
        symbols = _fetch_symbol_list_from_network(apikey)
    except Exception as error:
        cached = _fetch_cached_symbol_list(SYMBOL_LIST_CACHE_DAYS)
        if cached is not None:
            symbols, age = cached
            _loki.warning(
                f"Symbolilistan haku epaonnistui ({error}); kaytetaan {age} vrk "
                f"vanhaa valimuistia ({len(symbols)} symbolia)"
            )
            return symbols
        raise RuntimeError(
            f"Symbolilistan haku epaonnistui eika kelvollista valimuistia ole: {error}"
        ) from error

    # TODO tarkista myöhemmässä vaiheessa onko järkevä

    if len(symbols) < MIN_US_SYMBOL_COUNT:
        raise RuntimeError(
            f"Twelve Data /stocks palautti epauskottavan pienen symbolijoukon "
            f"({len(symbols)} kpl) - parsinta on todennakoisesti rikki"
        )
    # TODO

    _save_symbol_list_to_cache(symbols)
    _loki.info(
        f"twelvedata-stocks: haettu tuoreena, {len(symbols)} hyvaksyttya US-symbolia"
    )
    return symbols


def _is_valid_symbol(symbol) -> bool:
    return isinstance(symbol, str) and bool(SYMBOL_REGEX.match(symbol))


def _parse_iso_datetime(date_str):
    if not isinstance(date_str, str) or not date_str:
        return None
    try:
        moment = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _parse_date(date_str):
    if not isinstance(date_str, str) or not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except ValueError:
        return None


def _amount_upper_bound(amount) -> float | None:
    """Poimii ylärajan kongressidatan amount-kentästä, esim. "$100,001 - $250,000"."""
    if isinstance(amount, (int, float)):
        return float(amount)
    if not isinstance(amount, str):
        return None

    numbers = AMOUNT_NUMBER_REGEX.findall(amount)
    if not numbers:
        return None
    try:
        values = [float(number.replace(",", "")) for number in numbers]
    except ValueError:
        return None
    return max(values) if values else None


def _is_upgrade_by_rating_comparison(
    new_rating: str, previous_rating: str
) -> bool | None:
    """Vertailee kahta luokitusta, None jos jompikumpi on tuntematon."""
    u = RATING_ORDER.get(new_rating.strip().lower())
    e = RATING_ORDER.get(previous_rating.strip().lower())
    if u is None or e is None:
        return None
    return u > e


def _price_too_low(price) -> bool:
    """Kertoo onko hinta alle MIN_SHARE_PRICEn.

    Tuntematon/jäsentämätön hinta EI ole liian matala - se päästetään läpi,
    koska puuttuva tieto ei ole sama asia kuin huono tieto.
    """
    try:
        return float(price) < MIN_SHARE_PRICE
    except (TypeError, ValueError):
        return False


def _extract_price(row: dict, field: str) -> float | None:
    """Poimii hinnan riviltä puolustavasti viiteostohintaa varten.

    Puuttuva, jäsentymätön tai ei-positiivinen arvo palauttaa None eikä
    nosta poikkeusta.
    """
    try:
        price = float(row.get(field))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


# grades-latest-news-datan action-kentan tunnetut arvot. Downgrade hylkaa
# rivin kokonaan, muut kasitellaan omina tapauksinaan alla.
KNOWN_ACTIONS = {"upgrade", "initialise", "hold", "downgrade"}


def _score_analyst_row(row: dict, now: datetime) -> tuple[int, str] | None:
    """Pisteyttää yhden analyytikkorivin, tai None jos rivi hylätään kokonaan."""
    if _price_too_low(row.get("priceWhenPosted")):
        return None

    published = _parse_iso_datetime(row.get("publishedDate"))
    if published is None:
        return None
    if (now - published).days > MAX_ANALYST_AGE_DAYS:
        return None

    new_rating = str(row.get("newGrade") or "").strip()
    previous_rating = str(row.get("previousGrade") or "").strip()
    company = str(row.get("gradingCompany") or "Analyytikko").strip()
    symbol = str(row.get("symbol") or "?").strip()

    action = row.get("action")
    action_text = str(action).strip().lower() if action else None
    unknown = False

    if action_text is not None and action_text not in KNOWN_ACTIONS:
        _loki.warning(
            f"grades-latest-news: tuntematon action-arvo {action!r} symbolilla "
            f"{symbol} - kasitellaan neutraalina (hold)"
        )
        unknown = True
        action_text = "hold"

    if action_text == "downgrade":
        return None

    if action_text is None:
        # Action-kenttaa ei ole lainkaan riviIla - paatellaan korotus
        # luokkavertailulla, tuntematon kohdellaan aina hold:na (ei tata haaraa).
        action_text = (
            "upgrade"
            if _is_upgrade_by_rating_comparison(new_rating, previous_rating)
            else "hold"
        )

    score = 0
    if action_text == "upgrade":
        score += 3  # vanha nakemys parani - vahvin signaali
    elif action_text == "initialise":
        score += 2  # uusi seuranta aloitettu - tuore mutta ei mielenmuutosta
    elif action_text == "hold":
        score += 1  # mikaan ei muuttunut - heikoin

    if new_rating.lower() in ACCEPTED_RATINGS:
        score += 2

    if unknown:
        reason = f"{company} muutti luokitusta: {new_rating or '?'}"
    elif action_text == "upgrade":
        reason = f"{company} korotti: {previous_rating or '?'} -> {new_rating or '?'}"
    elif action_text == "initialise":
        reason = f"{company} aloitti seurannan: {new_rating or '?'}"
    else:
        reason = f"{company} piti ennallaan: {new_rating or '?'}"

    return score, reason


def _score_congress_row(row: dict, today: date) -> tuple[int, str]:
    """Pisteyttää yhden kongressiostorivin summan ja tuoreuden perusteella."""
    score = 1  # rivi lapaisi jo suodatuksen (osto)

    upper_bound = _amount_upper_bound(row.get("amount"))
    if upper_bound is not None:
        if upper_bound >= 50000:
            score += 2
        elif upper_bound >= 15000:
            score += 1

    transaction_date = _parse_date(row.get("transactionDate"))
    if transaction_date is not None:
        age_days = (today - transaction_date).days
        if age_days < 30:
            score += 2
        elif age_days < 60:
            score += 1

    date_text = row.get("transactionDate") or "?"
    if upper_bound is not None:
        limit_text = f"{int(upper_bound):,}".replace(",", " ")
        reason = f"Kongressiosto {date_text}, enintaan {limit_text} USD"
    else:
        reason = f"Kongressiosto {date_text}"

    return score, reason


def _insider_value(row: dict) -> float | None:
    try:
        return float(row.get("securitiesTransacted")) * float(row.get("price"))
    except (TypeError, ValueError):
        return None


def _score_insider_row(row: dict, today: date) -> tuple[int, str] | None:
    """Pisteyttää yhden sisäpiiriostorivin arvon ja tuoreuden perusteella."""
    if _price_too_low(row.get("price")):
        return None

    score = 1  # rivi lapaisi jo suodatuksen (osto)

    value = _insider_value(row)
    if value is not None:
        if value >= 50000:
            score += 2
        elif value >= 15000:
            score += 1

    transaction_date = _parse_date(row.get("transactionDate"))
    if transaction_date is not None:
        age_days = (today - transaction_date).days
        if age_days < 30:
            score += 2
        elif age_days < 60:
            score += 1

    date_text = row.get("transactionDate") or "?"
    if value is not None:
        value_text = f"{int(value):,}".replace(",", " ")
        reason = f"Sisapiiriosto {date_text}, noin {value_text} USD"
    else:
        reason = f"Sisapiiriosto {date_text}"

    return score, reason


def _price_target_values(row: dict) -> tuple[float, float] | None:
    target = row.get("priceTarget")
    if target is None:
        target = row.get("adjPriceTarget")
    current_price = row.get("priceWhenPosted")
    try:
        return float(target), float(current_price)
    except (TypeError, ValueError):
        return None


def _score_price_target_row(row: dict, now: datetime) -> tuple[int, str] | None:
    """Pisteyttää yhden tavoitehintarivin nousuvaran uskottavuuden perusteella."""
    if _price_too_low(row.get("priceWhenPosted")):
        return None

    published = _parse_iso_datetime(row.get("publishedDate"))
    if published is None:
        return None
    if (now - published).days > MAX_ANALYST_AGE_DAYS:
        return None

    values = _price_target_values(row)
    if values is None:
        return None
    target, current_price = values

    if current_price <= 0 or target <= current_price:
        return None

    # Kaannetty U: uskottava nousuvara palkitaan, epauskottava ei. Yli 60 %:n
    # nousuvara kertoo kaytannossa lahes aina ahdinkotilanteesta tai
    # vanhentuneesta tavoitehinnasta, ei vahvasta nakemyksesta - siksi sita
    # kohdellaan heikkona signaalina (+1), ei vahvana (+3).
    upside_pct = (target - current_price) / current_price * 100
    if upside_pct > 60:
        score = 1
    elif upside_pct >= 15:
        score = 3
    elif upside_pct >= 5:
        score = 2
    else:
        score = 1

    company = str(row.get("analystCompany") or "Analyytikko").strip()
    date_text = str(row.get("publishedDate") or "?")[:10]
    reason = (
        f"{company} tavoitehinta {target:g} USD, "
        f"+{upside_pct:.0f} % nykyhinnasta ({date_text})"
    )

    return score, reason


SOURCES = ("analyytikko", "kongressi", "sisapiiri", "tavoitehinta")


def _add_observation(
    results: dict,
    symbol: str,
    source: str,
    score: int,
    reason: str,
    price: float | None = None,
) -> None:
    entry = results.setdefault(symbol, {name: [] for name in SOURCES})
    entry[source].append((score, reason, price))


def _best(
    observations: list[tuple[int, str, float | None]],
) -> tuple[int, str | None, float | None]:
    if not observations:
        return 0, None, None
    # key vertailee vain pisteita (h[0]), joten hintoja (h[2], voi olla
    # None) ei koskaan verrata keskenaan.
    return max(observations, key=lambda h: h[0])


def _combine_symbol(symbol: str, entry: dict) -> dict:
    """Yhdistää yhden symbolin kaikkien lähteiden havainnot yhdeksi ehdokkaaksi.

    Pisteet = paras rivi per lähde + toistuvuusbonus + yhteisesiintymäbonus.
    Yhteisesiintymäbonus (+3) palkitsee kahden aidon signaalin yhtäpitävyyttä:
    se annetaan kerran jos vähintään kaksi lähdettä ylsivät itsenäisesti
    CO_OCCURRENCE_THRESHOLDiin - ei kahden heikon rivin sattumaa.
    """
    sources_present = [name for name in SOURCES if entry[name]]

    score = 0
    repeat_extra = 0
    reason_parts = []
    strong_sources = 0  # lahteet joiden oma paras rivi >= CO_OCCURRENCE_THRESHOLD
    best_by_source: dict = {}  # nimi -> (paras_pisteet, paras_hinta)

    for name in SOURCES:
        observations = entry[name]
        if not observations:
            continue
        best_score, best_reason, best_price = _best(observations)
        score += best_score
        best_by_source[name] = (best_score, best_price)

        if best_score >= CO_OCCURRENCE_THRESHOLD:
            strong_sources += 1

        extra = len(observations) - 1
        repeat_extra += extra

        if best_reason:
            part = best_reason
            if extra > 0:
                part += f" (+{extra} muuta mainintaa)"
            reason_parts.append(part)

    repeat_bonus = min(3, repeat_extra)
    both_bonus = 3 if strong_sources >= 2 else 0

    score += repeat_bonus + both_bonus

    # lahde-arvo kuvaa mista data tuli, ei pisteytysta - 'molemmat' merkitaan
    # aina kun useampi kuin yksi lahde loytyi, riippumatta bonuksesta.
    source = "molemmat" if len(sources_present) > 1 else sources_present[0]

    # viiteostohinta: vahvin lahde jolla on hinta voittaa (suurin
    # paras_pisteet jonka paras_hinta ei ole None). Tasapisteissa ratkaisee
    # LAHTEET-jarjestys (kayty lapi jarjestyksessa, vaihdetaan vain jos
    # aidosti parempi loytyy), jotta tulos on toistettava. Jos yhdellakaan
    # lahteella ei ole hintaa, jaa None.
    reference_price = None
    best_price_score = None
    for name in SOURCES:
        if name not in best_by_source:
            continue
        candidate_score, candidate_price = best_by_source[name]
        if candidate_price is None:
            continue
        if best_price_score is None or candidate_score > best_price_score:
            best_price_score = candidate_score
            reference_price = candidate_price

    return {
        "symbol": symbol,
        "score": score,
        "source": source,
        "reason": "; ".join(reason_parts),
        "reference_price": reference_price,
    }


def build_candidates(
    analyst_rows: list[dict],
    congress_rows: list[dict],
    insider_rows: list[dict],
    price_target_rows: list[dict],
    skip_symbols: list[str],
    us_symbols: set[str],
) -> list[dict]:
    """Pisteyttää neljän lähteen rivit ja yhdistää ne symbolikohtaisiksi ehdokkaiksi.

    Palauttaa enintään CANDIDATE_TARGET ehdokasta pistejärjestyksessä, joista
    parhaat merkitään jatkoon (advanced=1) CANDIDATE_THRESHOLD ja MAX_SELECTED
    -rajojen mukaisesti.
    """
    skip = set(skip_symbols or [])
    allowed = set(us_symbols or [])
    now = datetime.now(timezone.utc)
    today = now.date()

    results: dict = {}

    for row in analyst_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not _is_valid_symbol(symbol) or symbol in skip or symbol not in allowed:
            continue
        result = _score_analyst_row(row, now)
        if result is None:
            continue
        score, reason = result
        price = _extract_price(row, "priceWhenPosted")
        _add_observation(results, symbol, "analyytikko", score, reason, price)

    for row in congress_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not _is_valid_symbol(symbol) or symbol in skip or symbol not in allowed:
            continue
        type_ = str(row.get("type") or "").strip().lower()
        if type_ != "purchase":
            continue
        score, reason = _score_congress_row(row, today)
        _add_observation(results, symbol, "kongressi", score, reason)

    for row in insider_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not _is_valid_symbol(symbol) or symbol in skip or symbol not in allowed:
            continue
        direction = str(row.get("transactionType") or "").strip().upper()
        if not direction.startswith("P-"):
            continue
        result = _score_insider_row(row, today)
        if result is None:
            continue
        score, reason = result
        _add_observation(results, symbol, "sisapiiri", score, reason)

    for row in price_target_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not _is_valid_symbol(symbol) or symbol in skip or symbol not in allowed:
            continue
        result = _score_price_target_row(row, now)
        if result is None:
            continue
        score, reason = result
        price = _extract_price(row, "priceWhenPosted")
        _add_observation(results, symbol, "tavoitehinta", score, reason, price)

    candidates = [_combine_symbol(symbol, entry) for symbol, entry in results.items()]

    candidates.sort(key=lambda e: e["score"], reverse=True)
    candidates = candidates[:CANDIDATE_TARGET]

    for i, candidate in enumerate(candidates, start=1):
        candidate["rank"] = i

    # jatkoon: vahintaan CANDIDATE_THRESHOLD pistetta, korkeintaan MAX_SELECTED
    # kpl parhaasta paasta. Lista on jo pistejarjestyksessa, joten kynnyksen
    # ylittavat ehdokkaat muodostavat aina listan alun - riittaa laskea
    # kuinka moni ylittaa kynnyksen ja rajata se MAX_SELECTED-maaraan.
    above_threshold = sum(1 for e in candidates if e["score"] >= CANDIDATE_THRESHOLD)
    advanced_count = min(above_threshold, MAX_SELECTED)
    for i, candidate in enumerate(candidates):
        candidate["advanced"] = 1 if i < advanced_count else 0

    return candidates


def _run_check(run_id: str, run_type: str, db_path) -> str:
    """Merkitsee kaikki aktiiviset positiot jatkoon kahden viikon tarkistusta varten."""
    open_positions = tietokanta.get_open_positions(db_path)

    for position in open_positions:
        tietokanta.insert_candidate(
            run_id=run_id,
            run_type=run_type,
            symbol=position["symbol"],
            source="positio",
            reason="Kahden viikon tarkistus",
            advanced=1,
            db_path=db_path,
        )

    summary = f"{len(open_positions)} aktiivista positiota tarkistukseen"
    _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    return summary


def _run_normal(run_id: str, run_type: str, db_path) -> str:
    """Hakee kaikki neljä lähdettä, muodostaa ehdokkaat ja kirjaa ne kantaan.

    Nostaa RuntimeErrorin jos kaikki neljä lähdettä epäonnistuvat, tai jos
    US-symbolisuodatus epäonnistuu (sille ei ole "yksi lähde saa epäonnistua"
    -toleranssia).
    """
    open_positions = tietokanta.get_open_positions(db_path)
    skip_symbols = [p["symbol"] for p in open_positions]

    analyst_rows: list[dict] = []
    congress_rows: list[dict] = []
    insider_rows: list[dict] = []
    price_target_rows: list[dict] = []
    failed = []

    try:
        analyst_rows = fetch_analyst_changes(run_id)
    except Exception as error:
        _loki.warning(f"Analyytikkodatan haku epaonnistui: {error}")
        failed.append("analyytikko")

    try:
        congress_rows = fetch_congress_trades(run_id)
    except Exception as error:
        _loki.warning(f"Kongressidatan haku epaonnistui: {error}")
        failed.append("kongressi")

    try:
        insider_rows = fetch_insider_trades(run_id)
    except Exception as error:
        _loki.warning(f"Sisapiiridatan haku epaonnistui: {error}")
        failed.append("sisapiiri")

    try:
        price_target_rows = fetch_price_targets(run_id)
    except Exception as error:
        _loki.warning(f"Tavoitehintadatan haku epaonnistui: {error}")
        failed.append("tavoitehinta")

    if len(failed) == 4:
        raise RuntimeError(
            "Kaikki nelja lahdetta (analyytikko, kongressi, sisapiiri, tavoitehinta) "
            "epaonnistuivat"
        )

    # US-symbolisuodatus ei kuulu "yksi lahde saa epaonnistua" -saannon piiriin:
    # jos tama epaonnistuu, koko ajo keskeytyy tahan poikkeukseen.
    us_symbols = fetch_us_symbols()

    candidates = build_candidates(
        analyst_rows,
        congress_rows,
        insider_rows,
        price_target_rows,
        skip_symbols,
        us_symbols,
    )

    for candidate in candidates:
        tietokanta.insert_candidate(
            run_id=run_id,
            run_type=run_type,
            symbol=candidate["symbol"],
            source=candidate["source"],
            reason=candidate["reason"],
            score=candidate["score"],
            reason_is_valid=None,
            reason_note=None,
            reference_price=candidate.get("reference_price"),
            rank=candidate["rank"],
            advanced=candidate["advanced"],
            db_path=db_path,
        )

    advanced_count = sum(1 for e in candidates if e["advanced"] == 1)
    summary = f"{len(candidates)} ehdokasta, {advanced_count} jatkoon"
    _loki.info(f"Ajo {run_id} ({run_type}): {summary}")
    return summary


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Ajaa tarkistus- tai normaaliajon run_type:n mukaan."""
    if run_type == "tarkistus":
        return _run_check(run_id, run_type, db_path)
    return _run_normal(run_id, run_type, db_path)
