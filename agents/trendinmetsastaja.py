import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from core import loki, tietokanta
from core.asetukset import (
    EHDOKAS_KYNNYS,
    EHDOKAS_TAVOITE,
    MIN_OSAKEKURSSI,
    VALITTAVAT,
    YHTEISESIINTYMA_KYNNYS,
    hae_fmp_avain,
    hae_twelve_data_avain,
)

_loki = loki.hae_loki(__name__)

TIMEOUT = 10
FMP_BASE = "https://financialmodelingprep.com/stable"
TWELVE_DATA_STOCKS_URL = "https://api.twelvedata.com/stocks"
VALIMUISTI_DIR = tietokanta.DATA_DIR / "valimuisti"

SIVUN_KOKO = 10
# Talla FMP-tasolla vain page=0 on sallittu (katto on tilikohtainen, todettu
# koettimella - page=1 -> 402 kaikilla kolmella paatepisteella). Jos taso
# joskus paivittyy, riittaa kasvattaa tata lukua.
SIVUJA_MAX = 1

SYMBOLI_REGEX = re.compile(r"^[A-Z]{1,5}$")
HYVAKSYTYT_LUOKITUKSET = {"buy", "strong buy", "outperform", "overweight"}
AMOUNT_NUMERO_REGEX = re.compile(r"[\d][\d,]*")
MAKSIMI_IKA_ANALYYTIKKO_VRK = 14
MIN_US_SYMBOLIMAARA = 1000

# Twelve Datan /stocks-vastauksessa (country=United States) esiintyvat
# exchange-arvot: NASDAQ, NYSE, CBOE, IEX, OTC, XSTU (viimeinen on itse
# asiassa Stuttgartin porssi). Vain kaksi ensimmaista ovat US-paaporssit;
# loput hylataan (OTC nimenomaisesti pyydetysti, muut alaporssit/ulkomaiset
# samalla perusteella).
HYVAKSYTYT_PORSSIT = {"NASDAQ", "NYSE"}

# Koko maan /stocks-haku (country=United States, ei exchange-suodatinta)
# palauttaa yli 20 000 riviaa ja on megatavuja - eri suuruusluokkaa kuin
# FMP-haut, joten sille tarvitaan oma, pidempi timeout.
SYMBOLILISTA_TIMEOUT = 60
# Porssien symbolilista ei muutu paivittain (uusia listautumisia muutama
# viikossa), joten sille riittaa 30 vrk:n valimuisti - raskas haku tehdaan
# noin kerran kuukaudessa eika joka kylmalla ajolla.
SYMBOLILISTA_VALIMUISTI_VRK = 30
SYMBOLILISTA_TIEDOSTONIMI = "twelvedata-stocks"
# Jos yhden porssin exchange-suodatettu vastaus on nain iso, exchange-
# parametri on todennakoisesti jaanyt FMP:n/Twelve Datan paassa huomiotta
# (koko maan vastaus on ~20 000 riviaa, yksittaisen paaporssin pitaisi olla
# muutama tuhat).
EPAILYTTAVAN_SUURI_PORSSIVASTAUS = 10000

# Karkea jarjestysasteikko "korotus vs. laskenus" -paattelyyn silloin kun
# grades-latest-news-rivilta puuttuu oma action-kentta.
LUOKKA_JARJESTYS = {
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


def _hae_json_valimuistilla(nimi: str, url: str, params: dict):
    VALIMUISTI_DIR.mkdir(parents=True, exist_ok=True)
    tiedosto = VALIMUISTI_DIR / f"{nimi}_{date.today().isoformat()}.json"

    if tiedosto.exists():
        with open(tiedosto, encoding="utf-8") as tiedosto_kahva:
            return json.load(tiedosto_kahva)

    vastaus = requests.get(url, params=params, timeout=TIMEOUT)
    vastaus.raise_for_status()
    data = vastaus.json()

    with open(tiedosto, "w", encoding="utf-8") as tiedosto_kahva:
        json.dump(data, tiedosto_kahva)

    return data


def _hae_sivutettu(nimi: str, url: str, apikey: str) -> list[dict]:
    kaikki: list[dict] = []
    ensimmainen_sivu = None

    for sivu in range(SIVUJA_MAX):
        try:
            data = _hae_json_valimuistilla(
                f"{nimi}_sivu{sivu}", url, {"page": sivu, "limit": SIVUN_KOKO, "apikey": apikey}
            )
        except requests.RequestException:
            if sivu == 0:
                raise
            _loki.warning(
                f"{nimi}: sivu {sivu} epaonnistui, kaytetaan tahan mennessa saatuja "
                f"{len(kaikki)} rivia"
            )
            break

        if not isinstance(data, list):
            if sivu == 0:
                raise RuntimeError(f"{nimi} palautti odottamattoman muodon: {data!r}")
            _loki.warning(f"{nimi}: sivu {sivu} palautti odottamattoman muodon, lopetetaan sivutus")
            break

        if not data:
            break

        if sivu == 0:
            ensimmainen_sivu = data
        elif data == ensimmainen_sivu:
            _loki.warning(
                f"{nimi}: sivu {sivu} palautti samat rivit kuin sivu 0 - API ei nayta "
                f"tukevan sivutusta, lopetetaan"
            )
            break

        kaikki.extend(data)

    _loki.info(f"{nimi}: haettu {len(kaikki)} rivia")
    return kaikki


def hae_analyytikkomuutokset() -> list[dict]:
    apikey = hae_fmp_avain()
    return _hae_sivutettu("grades-latest-news", f"{FMP_BASE}/grades-latest-news", apikey)


def hae_kongressikaupat() -> list[dict]:
    apikey = hae_fmp_avain()
    senaatti = _hae_sivutettu("senate-latest", f"{FMP_BASE}/senate-latest", apikey)
    edustajat = _hae_sivutettu("house-latest", f"{FMP_BASE}/house-latest", apikey)
    return senaatti + edustajat


def hae_sisapiirikaupat() -> list[dict]:
    apikey = hae_fmp_avain()
    return _hae_sivutettu("insider-trading-latest", f"{FMP_BASE}/insider-trading/latest", apikey)


def hae_tavoitehinnat() -> list[dict]:
    apikey = hae_fmp_avain()
    return _hae_sivutettu(
        "price-target-latest-news", f"{FMP_BASE}/price-target-latest-news", apikey
    )


def _valimuistin_symbolilistatiedostot() -> list[Path]:
    if not VALIMUISTI_DIR.exists():
        return []
    return sorted(
        VALIMUISTI_DIR.glob(f"{SYMBOLILISTA_TIEDOSTONIMI}_*.json"), reverse=True
    )


def _hae_valimuistattu_symbolilista(max_ika_vrk: int) -> tuple[set[str], int] | None:
    tanaan = date.today()
    for tiedosto in _valimuistin_symbolilistatiedostot():
        pvm_str = tiedosto.stem.removeprefix(f"{SYMBOLILISTA_TIEDOSTONIMI}_")
        pvm = _jasenna_pvm(pvm_str)
        if pvm is None:
            continue

        ika = (tanaan - pvm).days
        if ika > max_ika_vrk:
            continue

        try:
            with open(tiedosto, encoding="utf-8") as tiedosto_kahva:
                symbolilista = json.load(tiedosto_kahva)
        except (OSError, ValueError):
            continue

        if not isinstance(symbolilista, list):
            continue

        return set(symbolilista), ika

    return None


def _tallenna_symbolilista_valimuistiin(symbolit: set[str]) -> None:
    VALIMUISTI_DIR.mkdir(parents=True, exist_ok=True)
    tiedosto = VALIMUISTI_DIR / f"{SYMBOLILISTA_TIEDOSTONIMI}_{date.today().isoformat()}.json"
    with open(tiedosto, "w", encoding="utf-8") as tiedosto_kahva:
        json.dump(sorted(symbolit), tiedosto_kahva)


def _hae_porssin_symbolit(porssi: str, apikey: str) -> list[dict]:
    vastaus = requests.get(
        TWELVE_DATA_STOCKS_URL,
        params={"country": "United States", "exchange": porssi, "apikey": apikey},
        timeout=SYMBOLILISTA_TIMEOUT,
    )
    vastaus.raise_for_status()
    data = vastaus.json()
    rivit = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rivit, list):
        raise RuntimeError(
            f"Twelve Data /stocks (exchange={porssi}) palautti odottamattoman muodon: {data!r}"
        )
    return rivit


def _hae_koko_maan_symbolit(apikey: str) -> list[dict]:
    vastaus = requests.get(
        TWELVE_DATA_STOCKS_URL,
        params={"country": "United States", "apikey": apikey},
        timeout=SYMBOLILISTA_TIMEOUT,
    )
    vastaus.raise_for_status()
    data = vastaus.json()
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RuntimeError(f"Twelve Data /stocks palautti odottamattoman muodon: {data!r}")
    return data["data"]


def _hae_symbolilista_verkosta(apikey: str) -> set[str]:
    porssikohtaiset: dict[str, list[dict]] = {}
    parametri_toimii = True

    for porssi in sorted(HYVAKSYTYT_PORSSIT):
        rivit = _hae_porssin_symbolit(porssi, apikey)
        if len(rivit) > EPAILYTTAVAN_SUURI_PORSSIVASTAUS:
            _loki.warning(
                f"Twelve Data /stocks?exchange={porssi} palautti {len(rivit)} rivia - "
                f"exchange-parametri nayttaa jaaneen huomiotta, palataan "
                f"suodattamattomaan hakuun"
            )
            parametri_toimii = False
            break
        porssikohtaiset[porssi] = rivit

    if parametri_toimii:
        symbolit = {
            str(rivi.get("symbol", "")).strip()
            for rivit in porssikohtaiset.values()
            for rivi in rivit
            if isinstance(rivi, dict)
        }
        symbolit.discard("")
        return symbolit

    kaikki_rivit = _hae_koko_maan_symbolit(apikey)
    symbolit = {
        str(rivi.get("symbol", "")).strip()
        for rivi in kaikki_rivit
        if isinstance(rivi, dict) and rivi.get("exchange") in HYVAKSYTYT_PORSSIT
    }
    symbolit.discard("")
    return symbolit


def hae_us_symbolit() -> set[str]:
    apikey = hae_twelve_data_avain()

    vara = _hae_valimuistattu_symbolilista(SYMBOLILISTA_VALIMUISTI_VRK)
    if vara is not None:
        symbolit, ika = vara
        _loki.info(
            f"twelvedata-stocks: kaytetaan {ika} vrk vanhaa valimuistia, "
            f"{len(symbolit)} symbolia"
        )
        return symbolit

    try:
        symbolit = _hae_symbolilista_verkosta(apikey)
    except Exception as virhe:
        vara = _hae_valimuistattu_symbolilista(SYMBOLILISTA_VALIMUISTI_VRK)
        if vara is not None:
            symbolit, ika = vara
            _loki.warning(
                f"Symbolilistan haku epaonnistui ({virhe}); kaytetaan {ika} vrk vanhaa "
                f"valimuistia ({len(symbolit)} symbolia)"
            )
            return symbolit
        raise RuntimeError(
            f"Symbolilistan haku epaonnistui eika kelvollista valimuistia ole: {virhe}"
        ) from virhe

    if len(symbolit) < MIN_US_SYMBOLIMAARA:
        raise RuntimeError(
            f"Twelve Data /stocks palautti epauskottavan pienen symbolijoukon "
            f"({len(symbolit)} kpl) - parsinta on todennakoisesti rikki"
        )

    _tallenna_symbolilista_valimuistiin(symbolit)
    _loki.info(f"twelvedata-stocks: haettu tuoreena, {len(symbolit)} hyvaksyttya US-symbolia")
    return symbolit


def _kelvollinen_symboli(symboli) -> bool:
    return isinstance(symboli, str) and bool(SYMBOLI_REGEX.match(symboli))


def _jasenna_iso_pvm(pvm_str):
    if not isinstance(pvm_str, str) or not pvm_str:
        return None
    try:
        aika = datetime.fromisoformat(pvm_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if aika.tzinfo is None:
        aika = aika.replace(tzinfo=timezone.utc)
    return aika


def _jasenna_pvm(pvm_str):
    if not isinstance(pvm_str, str) or not pvm_str:
        return None
    try:
        return date.fromisoformat(pvm_str[:10])
    except ValueError:
        return None


def _amount_ylaraja(amount) -> float | None:
    if isinstance(amount, (int, float)):
        return float(amount)
    if not isinstance(amount, str):
        return None

    luvut = AMOUNT_NUMERO_REGEX.findall(amount)
    if not luvut:
        return None
    try:
        arvot = [float(luku.replace(",", "")) for luku in luvut]
    except ValueError:
        return None
    return max(arvot) if arvot else None


def _on_korotus_luokkavertailulla(uusi: str, edellinen: str) -> bool | None:
    u = LUOKKA_JARJESTYS.get(uusi.strip().lower())
    e = LUOKKA_JARJESTYS.get(edellinen.strip().lower())
    if u is None or e is None:
        return None
    return u > e


def _hinta_liian_matala(hinta) -> bool:
    # Tuntematon/jasentamaton hinta EI ole liian matala - se paastetaan
    # lapi, koska puuttuva tieto ei ole sama asia kuin huono tieto.
    try:
        return float(hinta) < MIN_OSAKEKURSSI
    except (TypeError, ValueError):
        return False


# grades-latest-news-datan action-kentan tunnetut arvot. Downgrade hylkaa
# rivin kokonaan, muut kasitellaan omina tapauksinaan alla.
TUNNETUT_ACTIONIT = {"upgrade", "initialise", "hold", "downgrade"}


def _pisteyta_analyytikkorivi(rivi: dict, nyt: datetime) -> tuple[int, str] | None:
    if _hinta_liian_matala(rivi.get("priceWhenPosted")):
        return None

    pvm = _jasenna_iso_pvm(rivi.get("publishedDate"))
    if pvm is None:
        return None
    if (nyt - pvm).days > MAKSIMI_IKA_ANALYYTIKKO_VRK:
        return None

    uusi = str(rivi.get("newGrade") or "").strip()
    edellinen = str(rivi.get("previousGrade") or "").strip()
    yhtio = str(rivi.get("gradingCompany") or "Analyytikko").strip()
    symboli = str(rivi.get("symbol") or "?").strip()

    action = rivi.get("action")
    action_teksti = str(action).strip().lower() if action else None
    tuntematon = False

    if action_teksti is not None and action_teksti not in TUNNETUT_ACTIONIT:
        _loki.warning(
            f"grades-latest-news: tuntematon action-arvo {action!r} symbolilla "
            f"{symboli} - kasitellaan neutraalina (hold)"
        )
        tuntematon = True
        action_teksti = "hold"

    if action_teksti == "downgrade":
        return None

    if action_teksti is None:
        # Action-kenttaa ei ole lainkaan riviIla - paatellaan korotus
        # luokkavertailulla, tuntematon kohdellaan aina hold:na (ei tata haaraa).
        action_teksti = "upgrade" if _on_korotus_luokkavertailulla(uusi, edellinen) else "hold"

    pisteet = 0
    if action_teksti == "upgrade":
        pisteet += 3  # vanha nakemys parani - vahvin signaali
    elif action_teksti == "initialise":
        pisteet += 2  # uusi seuranta aloitettu - tuore mutta ei mielenmuutosta
    elif action_teksti == "hold":
        pisteet += 1  # mikaan ei muuttunut - heikoin

    if uusi.lower() in HYVAKSYTYT_LUOKITUKSET:
        pisteet += 2

    if tuntematon:
        syy = f"{yhtio} muutti luokitusta: {uusi or '?'}"
    elif action_teksti == "upgrade":
        syy = f"{yhtio} korotti: {edellinen or '?'} -> {uusi or '?'}"
    elif action_teksti == "initialise":
        syy = f"{yhtio} aloitti seurannan: {uusi or '?'}"
    else:
        syy = f"{yhtio} piti ennallaan: {uusi or '?'}"

    return pisteet, syy


def _pisteyta_kongressirivi(rivi: dict, tanaan: date) -> tuple[int, str]:
    pisteet = 1  # rivi lapaisi jo suodatuksen (osto)

    ylaraja = _amount_ylaraja(rivi.get("amount"))
    if ylaraja is not None:
        if ylaraja >= 50000:
            pisteet += 2
        elif ylaraja >= 15000:
            pisteet += 1

    pvm = _jasenna_pvm(rivi.get("transactionDate"))
    if pvm is not None:
        ika_paivina = (tanaan - pvm).days
        if ika_paivina < 30:
            pisteet += 2
        elif ika_paivina < 60:
            pisteet += 1

    pvm_teksti = rivi.get("transactionDate") or "?"
    if ylaraja is not None:
        raja_teksti = f"{int(ylaraja):,}".replace(",", " ")
        syy = f"Kongressiosto {pvm_teksti}, enintaan {raja_teksti} USD"
    else:
        syy = f"Kongressiosto {pvm_teksti}"

    return pisteet, syy


def _sisapiirin_arvo(rivi: dict) -> float | None:
    try:
        return float(rivi.get("securitiesTransacted")) * float(rivi.get("price"))
    except (TypeError, ValueError):
        return None


def _pisteyta_sisapiirirvi(rivi: dict, tanaan: date) -> tuple[int, str] | None:
    if _hinta_liian_matala(rivi.get("price")):
        return None

    pisteet = 1  # rivi lapaisi jo suodatuksen (osto)

    arvo = _sisapiirin_arvo(rivi)
    if arvo is not None:
        if arvo >= 50000:
            pisteet += 2
        elif arvo >= 15000:
            pisteet += 1

    pvm = _jasenna_pvm(rivi.get("transactionDate"))
    if pvm is not None:
        ika_paivina = (tanaan - pvm).days
        if ika_paivina < 30:
            pisteet += 2
        elif ika_paivina < 60:
            pisteet += 1

    pvm_teksti = rivi.get("transactionDate") or "?"
    if arvo is not None:
        arvo_teksti = f"{int(arvo):,}".replace(",", " ")
        syy = f"Sisapiiriosto {pvm_teksti}, noin {arvo_teksti} USD"
    else:
        syy = f"Sisapiiriosto {pvm_teksti}"

    return pisteet, syy


def _tavoitehinnan_arvot(rivi: dict) -> tuple[float, float] | None:
    tavoite = rivi.get("priceTarget")
    if tavoite is None:
        tavoite = rivi.get("adjPriceTarget")
    nykyhinta = rivi.get("priceWhenPosted")
    try:
        return float(tavoite), float(nykyhinta)
    except (TypeError, ValueError):
        return None


def _pisteyta_tavoitehintarivi(rivi: dict, nyt: datetime) -> tuple[int, str] | None:
    if _hinta_liian_matala(rivi.get("priceWhenPosted")):
        return None

    pvm = _jasenna_iso_pvm(rivi.get("publishedDate"))
    if pvm is None:
        return None
    if (nyt - pvm).days > MAKSIMI_IKA_ANALYYTIKKO_VRK:
        return None

    arvot = _tavoitehinnan_arvot(rivi)
    if arvot is None:
        return None
    tavoite, nykyhinta = arvot

    if nykyhinta <= 0 or tavoite <= nykyhinta:
        return None

    # Kaannetty U: uskottava nousuvara palkitaan, epauskottava ei. Yli 60 %:n
    # nousuvara kertoo kaytannossa lahes aina ahdinkotilanteesta tai
    # vanhentuneesta tavoitehinnasta, ei vahvasta nakemyksesta - siksi sita
    # kohdellaan heikkona signaalina (+1), ei vahvana (+3).
    nousu_pct = (tavoite - nykyhinta) / nykyhinta * 100
    if nousu_pct > 60:
        pisteet = 1
    elif nousu_pct >= 15:
        pisteet = 3
    elif nousu_pct >= 5:
        pisteet = 2
    else:
        pisteet = 1

    yhtio = str(rivi.get("analystCompany") or "Analyytikko").strip()
    pvm_teksti = str(rivi.get("publishedDate") or "?")[:10]
    syy = f"{yhtio} tavoitehinta {tavoite:g} USD, +{nousu_pct:.0f} % nykyhinnasta ({pvm_teksti})"

    return pisteet, syy


LAHTEET = ("analyytikko", "kongressi", "sisapiiri", "tavoitehinta")


def _lisaa_havainto(tulokset: dict, symboli: str, lahde: str, pisteet: int, syy: str) -> None:
    tieto = tulokset.setdefault(symboli, {nimi: [] for nimi in LAHTEET})
    tieto[lahde].append((pisteet, syy))


def _paras(havainnot: list[tuple[int, str]]) -> tuple[int, str | None]:
    if not havainnot:
        return 0, None
    return max(havainnot, key=lambda h: h[0])


def _yhdista_symboli(symboli: str, tieto: dict) -> dict:
    lahteet_mukana = [nimi for nimi in LAHTEET if tieto[nimi]]

    pisteet = 0
    toistuvuus_ekstra = 0
    syy_osat = []
    vahvat_lahteet = 0  # lahteet joiden oma paras rivi >= YHTEISESIINTYMA_KYNNYS

    for nimi in LAHTEET:
        havainnot = tieto[nimi]
        if not havainnot:
            continue
        paras_pisteet, paras_syy = _paras(havainnot)
        pisteet += paras_pisteet

        if paras_pisteet >= YHTEISESIINTYMA_KYNNYS:
            vahvat_lahteet += 1

        ekstra = len(havainnot) - 1
        toistuvuus_ekstra += ekstra

        if paras_syy:
            osa = paras_syy
            if ekstra > 0:
                osa += f" (+{ekstra} muuta mainintaa)"
            syy_osat.append(osa)

    toistuvuusbonus = min(3, toistuvuus_ekstra)
    # +3 kerran jos VAHINTAAN KAKSI lahdetta antoivat itsenaisesti vahintaan
    # YHTEISESIINTYMA_KYNNYS pistetta. Bonus palkitsee kahden OIKEAN
    # signaalin yhtapitavyyden, ei kahden heikon rivin sattumaa - 3 pistetta
    # on alin taso jolla lahde on sanonut jotain positiivista (esim. luokitus
    # Buy, tavoite 15-60 % ylapuolella, tai merkittava tuore osto). Sita
    # heikompi rivi on lasna mutta ei ole signaali.
    molemmat_bonus = 3 if vahvat_lahteet >= 2 else 0

    pisteet += toistuvuusbonus + molemmat_bonus

    # lahde-arvo kuvaa mista data tuli, ei pisteytysta - 'molemmat' merkitaan
    # aina kun useampi kuin yksi lahde loytyi, riippumatta bonuksesta.
    lahde = "molemmat" if len(lahteet_mukana) > 1 else lahteet_mukana[0]

    return {
        "symboli": symboli,
        "pisteet": pisteet,
        "lahde": lahde,
        "syy": "; ".join(syy_osat),
    }


def muodosta_ehdokkaat(
    analyytikkorivit: list[dict],
    kongressirivit: list[dict],
    sisapiirirvit: list[dict],
    tavoitehintarivit: list[dict],
    ohita_symbolit: list[str],
    us_symbolit: set[str],
) -> list[dict]:
    ohita = set(ohita_symbolit or [])
    sallitut = set(us_symbolit or [])
    nyt = datetime.now(timezone.utc)
    tanaan = nyt.date()

    tulokset: dict = {}

    for rivi in analyytikkorivit:
        symboli = str(rivi.get("symbol") or "").strip()
        if not _kelvollinen_symboli(symboli) or symboli in ohita or symboli not in sallitut:
            continue
        tulos = _pisteyta_analyytikkorivi(rivi, nyt)
        if tulos is None:
            continue
        pisteet, syy = tulos
        _lisaa_havainto(tulokset, symboli, "analyytikko", pisteet, syy)

    for rivi in kongressirivit:
        symboli = str(rivi.get("symbol") or "").strip()
        if not _kelvollinen_symboli(symboli) or symboli in ohita or symboli not in sallitut:
            continue
        tyyppi = str(rivi.get("type") or "").strip().lower()
        if tyyppi != "purchase":
            continue
        pisteet, syy = _pisteyta_kongressirivi(rivi, tanaan)
        _lisaa_havainto(tulokset, symboli, "kongressi", pisteet, syy)

    for rivi in sisapiirirvit:
        symboli = str(rivi.get("symbol") or "").strip()
        if not _kelvollinen_symboli(symboli) or symboli in ohita or symboli not in sallitut:
            continue
        suunta = str(rivi.get("transactionType") or "").strip().upper()
        if not suunta.startswith("P-"):
            continue
        tulos = _pisteyta_sisapiirirvi(rivi, tanaan)
        if tulos is None:
            continue
        pisteet, syy = tulos
        _lisaa_havainto(tulokset, symboli, "sisapiiri", pisteet, syy)

    for rivi in tavoitehintarivit:
        symboli = str(rivi.get("symbol") or "").strip()
        if not _kelvollinen_symboli(symboli) or symboli in ohita or symboli not in sallitut:
            continue
        tulos = _pisteyta_tavoitehintarivi(rivi, nyt)
        if tulos is None:
            continue
        pisteet, syy = tulos
        _lisaa_havainto(tulokset, symboli, "tavoitehinta", pisteet, syy)

    ehdokkaat = [_yhdista_symboli(symboli, tieto) for symboli, tieto in tulokset.items()]

    ehdokkaat.sort(key=lambda e: e["pisteet"], reverse=True)
    ehdokkaat = ehdokkaat[:EHDOKAS_TAVOITE]

    for i, ehdokas in enumerate(ehdokkaat, start=1):
        ehdokas["sijoitus"] = i

    # jatkoon: vahintaan EHDOKAS_KYNNYS pistetta, korkeintaan VALITTAVAT kpl
    # parhaasta paasta. Lista on jo pistejarjestyksessa, joten kynnyksen
    # ylittavat ehdokkaat muodostavat aina listan alun - riittaa laskea
    # kuinka moni ylittaa kynnyksen ja rajata se VALITTAVAT-maaraan.
    kynnyksen_ylittavia = sum(1 for e in ehdokkaat if e["pisteet"] >= EHDOKAS_KYNNYS)
    jatkoon_maara = min(kynnyksen_ylittavia, VALITTAVAT)
    for i, ehdokas in enumerate(ehdokkaat):
        ehdokas["jatkoon"] = 1 if i < jatkoon_maara else 0

    return ehdokkaat


def _aja_tarkistus(ajo_id: str, ajotyyppi: str, polku) -> str:
    aktiiviset = tietokanta.hae_aktiiviset_positiot(polku)

    for positio in aktiiviset:
        tietokanta.lisaa_ehdokas(
            ajo_id=ajo_id,
            ajotyyppi=ajotyyppi,
            symboli=positio["symboli"],
            lahde="positio",
            syy="Kahden viikon tarkistus",
            jatkoon=1,
            polku=polku,
        )

    yhteenveto = f"{len(aktiiviset)} aktiivista positiota tarkistukseen"
    _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
    return yhteenveto


def _aja_normaali(ajo_id: str, ajotyyppi: str, polku) -> str:
    aktiiviset = tietokanta.hae_aktiiviset_positiot(polku)
    ohita_symbolit = [p["symboli"] for p in aktiiviset]

    analyytikkorivit: list[dict] = []
    kongressirivit: list[dict] = []
    sisapiirirvit: list[dict] = []
    tavoitehintarivit: list[dict] = []
    epaonnistuneet = []

    try:
        analyytikkorivit = hae_analyytikkomuutokset()
    except Exception as virhe:
        _loki.warning(f"Analyytikkodatan haku epaonnistui: {virhe}")
        epaonnistuneet.append("analyytikko")

    try:
        kongressirivit = hae_kongressikaupat()
    except Exception as virhe:
        _loki.warning(f"Kongressidatan haku epaonnistui: {virhe}")
        epaonnistuneet.append("kongressi")

    try:
        sisapiirirvit = hae_sisapiirikaupat()
    except Exception as virhe:
        _loki.warning(f"Sisapiiridatan haku epaonnistui: {virhe}")
        epaonnistuneet.append("sisapiiri")

    try:
        tavoitehintarivit = hae_tavoitehinnat()
    except Exception as virhe:
        _loki.warning(f"Tavoitehintadatan haku epaonnistui: {virhe}")
        epaonnistuneet.append("tavoitehinta")

    if len(epaonnistuneet) == 4:
        raise RuntimeError(
            "Kaikki nelja lahdetta (analyytikko, kongressi, sisapiiri, tavoitehinta) "
            "epaonnistuivat"
        )

    # US-symbolisuodatus ei kuulu "yksi lahde saa epaonnistua" -saannon piiriin:
    # jos tama epaonnistuu, koko ajo keskeytyy tahan poikkeukseen.
    us_symbolit = hae_us_symbolit()

    ehdokkaat = muodosta_ehdokkaat(
        analyytikkorivit,
        kongressirivit,
        sisapiirirvit,
        tavoitehintarivit,
        ohita_symbolit,
        us_symbolit,
    )

    for ehdokas in ehdokkaat:
        tietokanta.lisaa_ehdokas(
            ajo_id=ajo_id,
            ajotyyppi=ajotyyppi,
            symboli=ehdokas["symboli"],
            lahde=ehdokas["lahde"],
            syy=ehdokas["syy"],
            pisteet=ehdokas["pisteet"],
            onko_syy_jarkeva=None,
            syy_perustelu=None,
            viiteostohinta=None,
            sijoitus=ehdokas["sijoitus"],
            jatkoon=ehdokas["jatkoon"],
            polku=polku,
        )

    jatkoon_maara = sum(1 for e in ehdokkaat if e["jatkoon"] == 1)
    yhteenveto = f"{len(ehdokkaat)} ehdokasta, {jatkoon_maara} jatkoon"
    _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
    return yhteenveto


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    if ajotyyppi == "tarkistus":
        return _aja_tarkistus(ajo_id, ajotyyppi, polku)
    return _aja_normaali(ajo_id, ajotyyppi, polku)
