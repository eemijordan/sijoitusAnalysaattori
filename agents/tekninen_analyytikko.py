import json
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from core import loki, tietokanta
from core.asetukset import MIN_OSAKEKURSSI, MIN_PAIVAVAIHTO, hae_twelve_data_avain

_loki = loki.hae_loki(__name__)


class KeskenerainenKurssidataVirhe(RuntimeError):
    pass


TIMEOUT = 10
TWELVE_DATA_BASE = "https://api.twelvedata.com"

# Ilmaistaso: 8 krediittia minuutissa, liukuva laskuri jota myos
# epaonnistuneet kutsut kasvattavat. Odotetaan tama joka kutsun jalkeen
# jotta pysytaan rajan alla ilman erillista kirjanpitoa.
TAHDISTUS_SEKUNTIA = 8
# Jos 429 tulee silti vastaan, odotetaan koko minuutti ja yritetaan samaa
# kutsua uudelleen tasan kerran.
RAJA_ODOTUS_SEKUNTIA = 60

KURSSIHISTORIA_PAIVIA = 252
SMA50_JAKSO = 50
SMA200_JAKSO = 200


def _on_rajavirhe(vastaus: requests.Response, data) -> bool:
    return vastaus.status_code == 429 or (isinstance(data, dict) and data.get("code") == 429)


def _hae_twelvedata_json(url: str, params: dict) -> dict:
    """Tekee GET-kutsun ja odottaa TAHDISTUS_SEKUNTIA sen jalkeen (aina, myos
    virheen jalkeen). Jos vastaus on 429, odottaa RAJA_ODOTUS_SEKUNTIA ja
    yrittaa samaa kutsua uudelleen kerran. Nostaa poikkeuksen jos toinenkin
    yritys epaonnistuu 429:aan tai jos HTTP-kutsu epaonnistuu muuten."""
    vastaus = requests.get(url, params=params, timeout=TIMEOUT)
    try:
        data = vastaus.json()
    except ValueError:
        data = None

    if _on_rajavirhe(vastaus, data):
        _loki.warning(
            f"Twelve Data 429 (minuuttiraja), odotetaan {RAJA_ODOTUS_SEKUNTIA}s ja "
            f"yritetaan uudelleen: {url}"
        )
        time.sleep(RAJA_ODOTUS_SEKUNTIA)

        vastaus = requests.get(url, params=params, timeout=TIMEOUT)
        try:
            data = vastaus.json()
        except ValueError:
            data = None
        time.sleep(TAHDISTUS_SEKUNTIA)

        if _on_rajavirhe(vastaus, data):
            raise RuntimeError("Twelve Data 429-raja tuli vastaan kahdesti perakkain")

        vastaus.raise_for_status()
        return data

    time.sleep(TAHDISTUS_SEKUNTIA)
    vastaus.raise_for_status()
    return data


def onko_palkki_valmis(palkin_pvm: date, tanaan_et: date) -> tuple[bool, str]:
    if palkin_pvm < tanaan_et:
        return True, ""
    syy = (
        f"viimeisin palkki {palkin_pvm.isoformat()} on kuluva parssipaiva "
        f"(New Yorkissa {tanaan_et.isoformat()}) - data on kesken"
    )
    return False, syy


def _laske_kurssitunnusluvut(paivat: list[dict]) -> dict:
    # Twelve Datan time_series palauttaa paivat uusin ensin.
    sulkuhinnat = [float(p["close"]) for p in paivat]
    korkeimmat = [float(p["high"]) for p in paivat]
    matalimmat = [float(p["low"]) for p in paivat]
    # Paivavaihto dollareissa = volyymi * paatoskurssi. Tama on korvike
    # markkina-arvolle, jota ei ole saatavilla ilmaistasolla.
    paivavaihdot_usd = [
        float(p["volume"]) * float(p["close"])
        for p in paivat
        if p.get("volume") not in (None, "")
    ]

    sma50 = (
        sum(sulkuhinnat[:SMA50_JAKSO]) / SMA50_JAKSO
        if len(sulkuhinnat) >= SMA50_JAKSO
        else None
    )
    sma200 = (
        sum(sulkuhinnat[:SMA200_JAKSO]) / SMA200_JAKSO
        if len(sulkuhinnat) >= SMA200_JAKSO
        else None
    )

    return {
        "nykyhinta": sulkuhinnat[0],
        "sma50": sma50,
        "sma200": sma200,
        "ylin_52vk": max(korkeimmat),
        "alin_52vk": min(matalimmat),
        "keskimaarainen_paivavaihto_usd": (
            sum(paivavaihdot_usd) / len(paivavaihdot_usd) if paivavaihdot_usd else None
        ),
        "historia_paivia": len(sulkuhinnat),
        "viimeisin_palkki": paivat[0].get("datetime"),
    }


def hae_kurssidata(symboli: str) -> dict | None:
    apikey = hae_twelve_data_avain()
    data = _hae_twelvedata_json(
        f"{TWELVE_DATA_BASE}/time_series",
        {
            "symbol": symboli,
            "interval": "1day",
            "outputsize": KURSSIHISTORIA_PAIVIA,
            "apikey": apikey,
        },
    )

    paivat = data.get("values") if isinstance(data, dict) else None
    if not isinstance(paivat, list) or not paivat:
        return None

    # Twelve Data palauttaa paivat uusin ensin ilman end_date-parametria.
    # Porssin ollessa auki paivat[0] on kesken oleva paivan palkki: sen
    # close, high, low ja volume eivat viela ole lopullisia, ja koko
    # indikaattorisarja laskettaisiin vajaasta datasta. Siksi uusimman
    # palkin on oltava aidosti aikaisempi kuin kuluva paiva New Yorkissa.
    palkin_pvm_str = paivat[0].get("datetime")
    palkin_pvm = None
    if isinstance(palkin_pvm_str, str):
        try:
            palkin_pvm = date.fromisoformat(palkin_pvm_str[:10])
        except ValueError:
            palkin_pvm = None

    if palkin_pvm is None:
        raise KeskenerainenKurssidataVirhe(
            f"{symboli}: viimeisimman palkin datetime-kentta puuttuu tai ei "
            f"jasenny ({palkin_pvm_str!r})"
        )

    tanaan_et = datetime.now(ZoneInfo("America/New_York")).date()
    valmis, syy = onko_palkki_valmis(palkin_pvm, tanaan_et)
    if not valmis:
        _loki.error(f"{symboli}: {syy}")
        raise KeskenerainenKurssidataVirhe(f"{symboli}: {syy}")

    return _laske_kurssitunnusluvut(paivat)


def _pura_indikaattoriarvo(data: dict, avain: str) -> float | None:
    if not isinstance(data, dict):
        return None
    arvot = data.get("values")
    if not isinstance(arvot, list) or not arvot:
        return None
    try:
        return float(arvot[0].get(avain))
    except (TypeError, ValueError):
        return None


def hae_atr(symboli: str) -> float | None:
    apikey = hae_twelve_data_avain()
    data = _hae_twelvedata_json(
        f"{TWELVE_DATA_BASE}/atr",
        {"symbol": symboli, "interval": "1day", "outputsize": 1, "apikey": apikey},
    )
    return _pura_indikaattoriarvo(data, "atr")


def hae_rsi(symboli: str) -> float | None:
    apikey = hae_twelve_data_avain()
    data = _hae_twelvedata_json(
        f"{TWELVE_DATA_BASE}/rsi",
        {"symbol": symboli, "interval": "1day", "outputsize": 1, "apikey": apikey},
    )
    return _pura_indikaattoriarvo(data, "rsi")


def _muodosta_perustelu(
    atr_pct, riski_pisteet, sma50, sma200, nykyhinta, trendi_pisteet,
    historia_paivia, asema_pct, asema_pisteet, rsi, rsi_pisteet, pisteet,
) -> str:
    if atr_pct is not None:
        atr_teksti = f"ATR {atr_pct:.1f} % ({riski_pisteet} p)".replace(".", ",")
    else:
        atr_teksti = f"ATR puuttuu ({riski_pisteet} p)"

    if sma50 is None:
        trendi_teksti = (
            f"SMA50 ei laskettavissa (vain {historia_paivia} paivaa historiaa) (0 p)"
        )
    elif sma200 is None:
        suunta = "yli SMA50" if nykyhinta > sma50 else "alle SMA50"
        trendi_teksti = (
            f"{suunta}, SMA200 ei laskettavissa (vain {historia_paivia} paivaa "
            f"historiaa) ({trendi_pisteet} p)"
        )
    elif nykyhinta > sma50 and sma50 > sma200:
        trendi_teksti = f"yli SMA50 ja SMA200 ({trendi_pisteet} p)"
    else:
        hinta_vs_sma50 = "yli SMA50" if nykyhinta > sma50 else "alle SMA50"
        sma50_vs_sma200 = "yli SMA200" if sma50 > sma200 else "alle SMA200"
        trendi_teksti = f"hinta {hinta_vs_sma50}, SMA50 {sma50_vs_sma200} ({trendi_pisteet} p)"

    if asema_pct is not None:
        asema_teksti = f"{asema_pct:.0f} % vaihteluvalista ({asema_pisteet} p)"
    else:
        asema_teksti = f"52 viikon vaihteluvali ei laskettavissa ({asema_pisteet} p)"

    if rsi is not None:
        rsi_teksti = f"RSI {rsi:.0f} ({rsi_pisteet} p)"
    else:
        rsi_teksti = f"RSI puuttuu ({rsi_pisteet} p)"

    pisteet_teksti = f"{pisteet:g}".replace(".", ",")
    return f"{atr_teksti}, {trendi_teksti}, {asema_teksti}, {rsi_teksti} = {pisteet_teksti}"


def laske_pisteet(kurssidata: dict, atr: float | None, rsi: float | None) -> tuple[float, dict, str]:
    nykyhinta = kurssidata["nykyhinta"]
    sma50 = kurssidata.get("sma50")
    sma200 = kurssidata.get("sma200")
    ylin_52vk = kurssidata.get("ylin_52vk")
    alin_52vk = kurssidata.get("alin_52vk")
    historia_paivia = kurssidata.get("historia_paivia")

    # RISKI: ATR suhteessa nykyhintaan.
    if atr is not None and nykyhinta > 0:
        atr_pct = atr / nykyhinta * 100
        if atr_pct < 2:
            riski_pisteet = 3
        elif atr_pct < 4:
            riski_pisteet = 2
        elif atr_pct < 6:
            riski_pisteet = 1
        else:
            riski_pisteet = 0
    else:
        atr_pct = None
        riski_pisteet = 0

    # TRENDI: hinta > SMA50 (+2), SMA50 > SMA200 (+2). Puuttuva SMA200 -
    # yleensa alle 200 pv historiaa - jattaa vain jalkimmaisen osan pois,
    # ei olettaa sita nollaksi aanettomasti (nakyy perustelussa).
    trendi_pisteet = 0
    if sma50 is not None and nykyhinta > sma50:
        trendi_pisteet += 2
    if sma50 is not None and sma200 is not None and sma50 > sma200:
        trendi_pisteet += 2

    # ASEMA 52 viikon vaihteluvalissa.
    if ylin_52vk is not None and alin_52vk is not None and ylin_52vk > alin_52vk:
        asema_pct = (nykyhinta - alin_52vk) / (ylin_52vk - alin_52vk) * 100
        if 40 <= asema_pct <= 85:
            asema_pisteet = 2
        elif asema_pct > 85:
            asema_pisteet = 1  # venynyt
        else:
            asema_pisteet = 0
    else:
        asema_pct = None
        asema_pisteet = 0

    # MOMENTTI (RSI).
    if rsi is not None:
        if 40 <= rsi <= 65:
            rsi_pisteet = 1
        elif rsi > 75:
            rsi_pisteet = -2
        elif rsi < 30:
            rsi_pisteet = -1
        else:
            rsi_pisteet = 0
    else:
        rsi_pisteet = 0

    pisteet_ennen_rajausta = riski_pisteet + trendi_pisteet + asema_pisteet + rsi_pisteet
    pisteet = max(0, min(10, pisteet_ennen_rajausta))

    indikaattorit = {
        "nykyhinta": nykyhinta,
        "atr": atr,
        "atr_pct": atr_pct,
        "riski_pisteet": riski_pisteet,
        "sma50": sma50,
        "sma200": sma200,
        "trendi_pisteet": trendi_pisteet,
        "ylin_52vk": ylin_52vk,
        "alin_52vk": alin_52vk,
        "asema_pct": asema_pct,
        "asema_pisteet": asema_pisteet,
        "rsi": rsi,
        "rsi_pisteet": rsi_pisteet,
        "historia_paivia": historia_paivia,
        "keskimaarainen_paivavaihto_usd": kurssidata.get("keskimaarainen_paivavaihto_usd"),
        "viimeisin_palkki": kurssidata.get("viimeisin_palkki"),
        "pisteet_ennen_rajausta": pisteet_ennen_rajausta,
    }

    perustelu = _muodosta_perustelu(
        atr_pct, riski_pisteet, sma50, sma200, nykyhinta, trendi_pisteet,
        historia_paivia, asema_pct, asema_pisteet, rsi, rsi_pisteet, pisteet,
    )

    return pisteet, indikaattorit, perustelu


def aja(ajo_id: str, ajotyyppi: str, polku=tietokanta.OLETUS_TIETOKANTA) -> str:
    # Normaali- ja tarkistusajo lukevat molemmat jatkoon=1-rivit samalla
    # tavalla - Agentti 1 on jo kirjoittanut aktiiviset positiot naiksi
    # tarkistusajossa, joten sama haara riittaa.
    ehdokkaat = tietokanta.hae_ehdokkaat(ajo_id, vain_jatkoon=True, polku=polku)

    kirjattu = 0
    ensimmainen_lokitettu = False
    for ehdokas in ehdokkaat:
        symboli = ehdokas["symboli"]
        try:
            kurssidata = hae_kurssidata(symboli)
            if kurssidata is None:
                _loki.warning(f"{symboli}: time_series ei palauttanut dataa, ohitetaan")
                continue

            if not ensimmainen_lokitettu:
                _loki.info(
                    f"Ajo {ajo_id}: viimeisin palkki ({symboli}) = "
                    f"{kurssidata.get('viimeisin_palkki')}"
                )
                ensimmainen_lokitettu = True

            nykyhinta = kurssidata["nykyhinta"]
            if nykyhinta < MIN_OSAKEKURSSI:
                _loki.info(
                    f"{symboli}: MIN_OSAKEKURSSI-raja hylkasi (hinta {nykyhinta} USD "
                    f"< {MIN_OSAKEKURSSI} USD), ohitetaan"
                )
                continue

            paivavaihto = kurssidata.get("keskimaarainen_paivavaihto_usd")
            if paivavaihto is not None and paivavaihto < MIN_PAIVAVAIHTO:
                _loki.info(
                    f"{symboli}: MIN_PAIVAVAIHTO-raja hylkasi (paivavaihto "
                    f"{paivavaihto:,.0f} USD < {MIN_PAIVAVAIHTO:,.0f} USD), ohitetaan"
                )
                continue

            atr = hae_atr(symboli)
            rsi = hae_rsi(symboli)

            pisteet, indikaattorit, perustelu = laske_pisteet(kurssidata, atr, rsi)

            tietokanta.lisaa_pisteytys(
                ajo_id=ajo_id,
                ajotyyppi=ajotyyppi,
                symboli=symboli,
                risk_reward_pisteet=pisteet,
                hinta=nykyhinta,
                indikaattorit_json=json.dumps(indikaattorit),
                perustelu=perustelu,
                polku=polku,
            )
            kirjattu += 1
        except KeskenerainenKurssidataVirhe:
            # Ei nielaista: jos porssi on auki, jokainen symboli on yhta
            # pilalla, joten koko ajon pitaa keskeytya eika vain ohittaa
            # yksi symboli. Orkestroija kaarii ja lokittaa taman.
            raise
        except Exception as virhe:
            _loki.warning(f"{symboli}: kasittely epaonnistui, ohitetaan - {virhe}")
            continue

    yhteenveto = f"{kirjattu} pisteytysta kirjattu"
    _loki.info(f"Ajo {ajo_id} ({ajotyyppi}): {yhteenveto}")
    return yhteenveto
