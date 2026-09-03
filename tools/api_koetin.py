from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from core.asetukset import get_fmp_key, get_twelve_data_key

TIMEOUT = 10
MAX_MERKKIA = 400

FMP_PAATEPISTEET = [
    (
        "senate-latest",
        "https://financialmodelingprep.com/stable/senate-latest?page=0&limit=10",
    ),
    (
        "house-latest",
        "https://financialmodelingprep.com/stable/house-latest?page=0&limit=10",
    ),
    (
        "grades-latest-news",
        "https://financialmodelingprep.com/stable/grades-latest-news?page=0&limit=10",
    ),
    (
        "grade-latest-news",
        "https://financialmodelingprep.com/stable/grade-latest-news?page=0&limit=10",
    ),
    (
        "price-target-latest-news",
        "https://financialmodelingprep.com/stable/price-target-latest-news?page=0&limit=10",
    ),
    (
        "insider-search-ilman",
        "https://financialmodelingprep.com/stable/insider-trading/search?page=0&limit=10",
    ),
    (
        "insider-search-ostot",
        "https://financialmodelingprep.com/stable/insider-trading/search?transactionType=P-Purchase&page=0&limit=10",
    ),
    (
        "senate-ostot",
        "https://financialmodelingprep.com/stable/senate-latest?page=0&limit=10&type=Purchase",
    ),
    (
        "insider-latest",
        "https://financialmodelingprep.com/stable/insider-trading/latest?page=0&limit=10",
    ),
    (
        "limit-testi-price-target-50",
        "https://financialmodelingprep.com/stable/price-target-latest-news?page=0&limit=50",
    ),
]

TWELVE_DATA_PAATEPISTEET = [
    ("quote", "https://api.twelvedata.com/quote?symbol=AAPL"),
    ("rsi", "https://api.twelvedata.com/rsi?symbol=AAPL&interval=1day&outputsize=1"),
    ("analyst_ratings", "https://api.twelvedata.com/analyst_ratings/light?symbol=AAPL"),
]

# Agentti 2:n pisteytys tarvitsee nama viisi Twelve Data -paatepistetta.
# Kahdella symbolilla: AAPL (suuryhtio, varmasti dataa) ja TKNO (pieni
# yhtio, oikea ehdokas aiemmasta ajosta) - jotta nahdaan toimiiko data
# myos pienelle nimelle, ei vain suurelle.
INDIKAATTORI_SYMBOLIT = ["AAPL", "TKNO"]
QUOTE_TARKISTETTAVAT_KENTAT = [
    "close",
    "fifty_two_week_high",
    "fifty_two_week_low",
    "average_volume",
]


def _twelvedata_indikaattori_url(nimi: str, symboli: str) -> str:
    if nimi == "quote":
        return f"https://api.twelvedata.com/quote?symbol={symboli}"
    if nimi == "atr":
        return f"https://api.twelvedata.com/atr?symbol={symboli}&interval=1day&outputsize=1"
    if nimi == "rsi":
        return f"https://api.twelvedata.com/rsi?symbol={symboli}&interval=1day&outputsize=1"
    if nimi == "sma50":
        return (
            f"https://api.twelvedata.com/sma?symbol={symboli}&interval=1day"
            f"&time_period=50&outputsize=1"
        )
    if nimi == "sma200":
        return (
            f"https://api.twelvedata.com/sma?symbol={symboli}&interval=1day"
            f"&time_period=200&outputsize=1"
        )
    raise ValueError(f"Tuntematon indikaattori: {nimi}")


# FMP:n historialliset tunnusluvut (P/E) - kolme symbolia joilla eri profiili.
FMP_RATIOS_SYMBOLIT = ["AAPL", "TKNO", "TCRX"]

# (nimi, suuntakentta) niille insider-/kongressipaatepisteille joiden
# rivien odotetaan kertovan oliko kyseessa osto vai myynti.
SUUNTAKENTAT = [
    ("senate-latest", "type"),
    ("house-latest", "type"),
    ("senate-ostot", "type"),
    ("insider-search-ilman", "transactionType"),
    ("insider-search-ostot", "transactionType"),
    ("insider-latest", "transactionType"),
]


def _turvallinen_url(url: str, apikey: str) -> str:
    pyynto = requests.Request("GET", url, params={"apikey": apikey}).prepare()
    parsed = urlparse(pyynto.url)
    parametrit = parse_qsl(parsed.query)
    peitetty = [
        (avain, "***" if avain.lower() == "apikey" else arvo)
        for avain, arvo in parametrit
    ]
    return urlunparse(parsed._replace(query=urlencode(peitetty)))


def _koeta(nimi: str, url: str, apikey: str):
    print(f"\n=== {nimi} ===")
    print(f"URL: {_turvallinen_url(url, apikey)}")

    try:
        vastaus = requests.get(url, params={"apikey": apikey}, timeout=TIMEOUT)
        print(f"HTTP-status: {vastaus.status_code}")

        try:
            data = vastaus.json()
        except ValueError:
            teksti = vastaus.text[:MAX_MERKKIA]
            print(
                f"Vastaus ei ole JSONia. Teksti (max {MAX_MERKKIA} merkkia): {teksti}"
            )
            return None

        if isinstance(data, list):
            tyyppi = "lista"
            rivimaara = len(data)
            ensimmainen = data[0] if data else None
        else:
            tyyppi = type(data).__name__
            rivimaara = 1
            ensimmainen = data

        print(f"Vastauksen tyyppi: {tyyppi}, rivimaara: {rivimaara}")

        if isinstance(ensimmainen, dict):
            print(f"Ensimmaisen rivin avaimet: {list(ensimmainen.keys())}")

        rivi_str = str(ensimmainen)
        if len(rivi_str) > MAX_MERKKIA:
            rivi_str = rivi_str[:MAX_MERKKIA] + "..."
        print(f"Ensimmainen rivi: {rivi_str}")

        return data

    except Exception as virhe:
        print(f"VIRHE: {virhe}")
        return None


def _symbolit(data: list) -> set:
    return {rivi.get("symbol") for rivi in data if isinstance(rivi, dict)}


def _jakauma(data: list, kentta: str) -> Counter:
    return Counter(rivi.get(kentta) for rivi in data if isinstance(rivi, dict))


def _tulosta_suuntajakaumat(tulokset: dict) -> None:
    print("\n########## Suunnan jakauma (kaikki rivit) ##########")
    for nimi, kentta in SUUNTAKENTAT:
        data = tulokset.get(nimi)
        if not isinstance(data, list):
            print(f"{nimi}: ei listamuotoista vastausta, ohitetaan")
            continue
        print(f"{nimi} ({kentta}): {dict(_jakauma(data, kentta))}")


def _vertaa_paria(
    nimi_ilman: str, nimi_suodatettu: str, kentta: str, tulokset: dict
) -> None:
    print(f"\n--- {nimi_ilman} vs {nimi_suodatettu} (suuntakentta: {kentta}) ---")

    data_ilman = tulokset.get(nimi_ilman)
    data_suodatettu = tulokset.get(nimi_suodatettu)

    if not isinstance(data_ilman, list) or not isinstance(data_suodatettu, list):
        print(
            "Jompikumpi tai molemmat vastaukset puuttuvat (ei listaa) - "
            "vertailua ei voi tehda."
        )
        return

    if data_ilman == data_suodatettu:
        print(
            "HUOM: rivit ovat TASMALLEEN IDENTTISET -> suodatinparametri jai huomiotta."
        )

    symbolit_ilman = _symbolit(data_ilman)
    symbolit_suodatettu = _symbolit(data_suodatettu)
    print(f"Symbolit tasmalleen samat: {symbolit_ilman == symbolit_suodatettu}")

    jakauma_ilman = _jakauma(data_ilman, kentta)
    jakauma_suodatettu = _jakauma(data_suodatettu, kentta)
    print(f"Suunnan jakauma ilman suodatinta: {dict(jakauma_ilman)}")
    print(f"Suunnan jakauma suodattimella:    {dict(jakauma_suodatettu)}")
    print(f"Jakauma muuttui: {jakauma_ilman != jakauma_suodatettu}")


def _tulosta_quote_kentat(tulokset: dict, symbolit: list) -> None:
    print("\n########## Quote-kenttien tarkistus (pisteytys nojaa naihin) ##########")
    for symboli in symbolit:
        data = tulokset.get(f"quote-{symboli}")
        print(f"\n{symboli}:")
        if not isinstance(data, dict):
            print(
                "  quote-vastaus puuttuu tai ei ole dict - ei voida tarkistaa kenttia"
            )
            continue
        for kentta in QUOTE_TARKISTETTAVAT_KENTAT:
            print(f"  {kentta}: {data.get(kentta, '<PUUTTUU>')}")
        print("  fifty_two_week (raaka arvo - ylla olevat 52vk-kentat saattavat olla")
        print(
            "  vaarin nimettyja, tama nayttaa oikean rakenteen): "
            f"{data.get('fifty_two_week')!r}"
        )


def _tulosta_sma200_tkno(tulokset: dict) -> None:
    print("\n########## sma200 TKNO:lle (200 pv historiaa vaatii) ##########")
    data = tulokset.get("sma200-TKNO")
    if data is None:
        print("sma200-TKNO: ei dataa (ks. VIRHE-rivi ylla).")
        return
    if isinstance(data, dict) and data.get("status") == "error":
        print(f"sma200-TKNO palautti virheen: {data.get('message', data)}")
        return
    if (
        isinstance(data, dict)
        and isinstance(data.get("values"), list)
        and not data["values"]
    ):
        print("sma200-TKNO palautti tyhjan values-listan - ei riittavasti historiaa.")
        return
    print(f"sma200-TKNO nayttaa toimivan, koko vastaus: {data}")


def _koeta_minuuttiraja(apikey: str) -> None:
    print(
        "\n########## Twelve Data minuuttiraja (12 perakkaista quote-kutsua) ##########"
    )
    url = "https://api.twelvedata.com/quote"
    ensimmainen_virhe = None

    for i in range(1, 13):
        try:
            vastaus = requests.get(
                url, params={"symbol": "AAPL", "apikey": apikey}, timeout=TIMEOUT
            )
            try:
                data = vastaus.json()
            except ValueError:
                data = None

            on_virhe = vastaus.status_code == 429 or (
                isinstance(data, dict) and data.get("status") == "error"
            )
            tiivistelma = str(data)[:200] if data is not None else vastaus.text[:200]
            merkki = " [VIRHE]" if on_virhe else ""
            print(f"Kutsu {i:>2}: HTTP {vastaus.status_code}{merkki} - {tiivistelma}")

            if on_virhe and ensimmainen_virhe is None:
                ensimmainen_virhe = i
        except Exception as virhe:
            print(f"Kutsu {i:>2}: POIKKEUS {virhe}")

    if ensimmainen_virhe is not None:
        print(f"\nRaja tuli vastaan kutsulla numero {ensimmainen_virhe}/12.")
    else:
        print("\nRajaa ei tullut vastaan 12 kutsun aikana.")


def _tulosta_pe_analyysi(tulokset: dict, symbolit: list) -> None:
    print("\n########## P/E-kentan tarkistus (FMP ratios / ratios-ttm) ##########")
    for symboli in symbolit:
        print(f"\n{symboli}:")
        for nimi in (f"ratios-{symboli}", f"ratios-ttm-{symboli}"):
            data = tulokset.get(nimi)

            if isinstance(data, list) and data:
                rivi = data[0]
                pe_avaimet = [avain for avain in rivi.keys() if "pe" in avain.lower()]
                print(
                    f"  {nimi}: {len(data)} rivia, "
                    f"mahdolliset P/E-avaimet: {pe_avaimet}"
                )
                for avain in pe_avaimet:
                    print(f"    {avain} = {rivi.get(avain)!r}")
            elif isinstance(data, dict):
                pe_avaimet = [avain for avain in data.keys() if "pe" in avain.lower()]
                print(f"  {nimi}: dict-vastaus, mahdolliset P/E-avaimet: {pe_avaimet}")
                for avain in pe_avaimet:
                    print(f"    {avain} = {data.get(avain)!r}")
            else:
                print(f"  {nimi}: ei kelvollista dataa (ks. VIRHE/vastaus ylla)")


def main() -> None:
    fmp_avain = get_fmp_key()
    twelve_data_avain = get_twelve_data_key()

    tulokset: dict = {}

    print("########## FMP ##########")
    for nimi, url in FMP_PAATEPISTEET:
        tulokset[nimi] = _koeta(nimi, url, fmp_avain)

    print("\n########## Twelve Data ##########")
    for nimi, url in TWELVE_DATA_PAATEPISTEET:
        _koeta(nimi, url, twelve_data_avain)

    _tulosta_suuntajakaumat(tulokset)

    # --- Kertaluontoinen suodatinvertailu: toimivatko transactionType/type
    # -parametrit? Oma erillinen lohko - helppo poistaa myohemmin yhtena palana. ---
    print("\n########## Suodatinvertailu (kertaluontoinen) ##########")
    _vertaa_paria(
        "insider-search-ilman", "insider-search-ostot", "transactionType", tulokset
    )
    _vertaa_paria("senate-latest", "senate-ostot", "type", tulokset)

    # --- Vaihe 2a: Twelve Data -indikaattorit AAPL:lle ja TKNO:lle ---
    print("\n########## Twelve Data -indikaattorit (AAPL + TKNO) ##########")
    for symboli in INDIKAATTORI_SYMBOLIT:
        for indikaattori in ("quote", "atr", "rsi", "sma50", "sma200"):
            nimi = f"{indikaattori}-{symboli}"
            url = _twelvedata_indikaattori_url(indikaattori, symboli)
            tulokset[nimi] = _koeta(nimi, url, twelve_data_avain)

    _tulosta_quote_kentat(tulokset, INDIKAATTORI_SYMBOLIT)
    _tulosta_sma200_tkno(tulokset)

    # --- Vaihe 2a: Twelve Datan minuuttiraja (12 perakkaista quote-kutsua) ---
    _koeta_minuuttiraja(twelve_data_avain)

    # --- Vaihe 2a: FMP:n historialliset tunnusluvut (P/E) ---
    print("\n########## FMP ratios / ratios-ttm (AAPL, TKNO, TCRX) ##########")
    for symboli in FMP_RATIOS_SYMBOLIT:
        tulokset[f"ratios-{symboli}"] = _koeta(
            f"ratios-{symboli}",
            f"https://financialmodelingprep.com/stable/ratios?symbol={symboli}&period=annual&limit=5",
            fmp_avain,
        )
        tulokset[f"ratios-ttm-{symboli}"] = _koeta(
            f"ratios-ttm-{symboli}",
            f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={symboli}",
            fmp_avain,
        )

    _tulosta_pe_analyysi(tulokset, FMP_RATIOS_SYMBOLIT)


if __name__ == "__main__":
    main()
