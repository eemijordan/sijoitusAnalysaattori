from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from core.asetukset import hae_fmp_avain, hae_twelve_data_avain

TIMEOUT = 10
MAX_MERKKIA = 400

FMP_PAATEPISTEET = [
    ("senate-latest", "https://financialmodelingprep.com/stable/senate-latest?page=0&limit=10"),
    ("house-latest", "https://financialmodelingprep.com/stable/house-latest?page=0&limit=10"),
    ("grades-latest-news", "https://financialmodelingprep.com/stable/grades-latest-news?page=0&limit=10"),
    ("grade-latest-news", "https://financialmodelingprep.com/stable/grade-latest-news?page=0&limit=10"),
    ("price-target-latest-news", "https://financialmodelingprep.com/stable/price-target-latest-news?page=0&limit=10"),
    ("insider-search-ilman", "https://financialmodelingprep.com/stable/insider-trading/search?page=0&limit=10"),
    ("insider-search-ostot", "https://financialmodelingprep.com/stable/insider-trading/search?transactionType=P-Purchase&page=0&limit=10"),
    ("senate-ostot", "https://financialmodelingprep.com/stable/senate-latest?page=0&limit=10&type=Purchase"),
    ("insider-latest", "https://financialmodelingprep.com/stable/insider-trading/latest?page=0&limit=10"),
    ("limit-testi-price-target-50", "https://financialmodelingprep.com/stable/price-target-latest-news?page=0&limit=50"),
]

TWELVE_DATA_PAATEPISTEET = [
    ("quote", "https://api.twelvedata.com/quote?symbol=AAPL"),
    ("rsi", "https://api.twelvedata.com/rsi?symbol=AAPL&interval=1day&outputsize=1"),
    ("analyst_ratings", "https://api.twelvedata.com/analyst_ratings/light?symbol=AAPL"),
]

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
            print(f"Vastaus ei ole JSONia. Teksti (max {MAX_MERKKIA} merkkia): {teksti}")
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


def _vertaa_paria(nimi_ilman: str, nimi_suodatettu: str, kentta: str, tulokset: dict) -> None:
    print(f"\n--- {nimi_ilman} vs {nimi_suodatettu} (suuntakentta: {kentta}) ---")

    data_ilman = tulokset.get(nimi_ilman)
    data_suodatettu = tulokset.get(nimi_suodatettu)

    if not isinstance(data_ilman, list) or not isinstance(data_suodatettu, list):
        print("Jompikumpi tai molemmat vastaukset puuttuvat (ei listaa) - vertailua ei voi tehda.")
        return

    if data_ilman == data_suodatettu:
        print("HUOM: rivit ovat TASMALLEEN IDENTTISET -> suodatinparametri jai huomiotta.")

    symbolit_ilman = _symbolit(data_ilman)
    symbolit_suodatettu = _symbolit(data_suodatettu)
    print(f"Symbolit tasmalleen samat: {symbolit_ilman == symbolit_suodatettu}")

    jakauma_ilman = _jakauma(data_ilman, kentta)
    jakauma_suodatettu = _jakauma(data_suodatettu, kentta)
    print(f"Suunnan jakauma ilman suodatinta: {dict(jakauma_ilman)}")
    print(f"Suunnan jakauma suodattimella:    {dict(jakauma_suodatettu)}")
    print(f"Jakauma muuttui: {jakauma_ilman != jakauma_suodatettu}")


def main() -> None:
    fmp_avain = hae_fmp_avain()
    twelve_data_avain = hae_twelve_data_avain()

    tulokset: dict = {}

    print("########## FMP ##########")
    for nimi, url in FMP_PAATEPISTEET:
        tulokset[nimi] = _koeta(nimi, url, fmp_avain)

    print("\n########## Twelve Data ##########")
    for nimi, url in TWELVE_DATA_PAATEPISTEET:
        _koeta(nimi, url, twelve_data_avain)

    _tulosta_suuntajakaumat(tulokset)

    # --- Kertaluontoinen suodatinvertailu: toimivatko transactionType/type -parametrit? ---
    # Oma erillinen lohko - helppo poistaa myohemmin yhtena palana.
    print("\n########## Suodatinvertailu (kertaluontoinen) ##########")
    _vertaa_paria("insider-search-ilman", "insider-search-ostot", "transactionType", tulokset)
    _vertaa_paria("senate-latest", "senate-ostot", "type", tulokset)


if __name__ == "__main__":
    main()
