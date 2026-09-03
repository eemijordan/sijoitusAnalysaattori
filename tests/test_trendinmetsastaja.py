import json
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from agents import trendinmetsastaja
from core import tietokanta
from core.asetukset import CANDIDATE_THRESHOLD, MAX_SELECTED

SYMBOLIT_12 = [
    "AAAA",
    "BBBB",
    "CCCC",
    "DDDD",
    "EEEE",
    "FFFF",
    "GGGG",
    "HHHH",
    "IIII",
    "JJJJ",
    "KKKK",
    "LLLL",
]
US_SYMBOLIT = {"AAPL", "MSFT", "TSLA", "GOOGL", "NVDA", "LILAK"} | set(SYMBOLIT_12)


def _analyytikkorivi(
    symboli,
    uusi="Buy",
    edellinen="Hold",
    action="upgrade",
    paivia_sitten=2,
    yhtio="Goldman Sachs",
    hinta=100.0,
):
    pvm = datetime.now(timezone.utc) - timedelta(days=paivia_sitten)
    return {
        "symbol": symboli,
        "publishedDate": pvm.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "newsURL": "https://example.com",
        "newsTitle": "esimerkkiotsikko",
        "newsBaseURL": "example.com",
        "newsPublisher": "TheFly",
        "newGrade": uusi,
        "previousGrade": edellinen,
        "gradingCompany": yhtio,
        "action": action,
        "priceWhenPosted": hinta,
    }


def _kongressirivi(
    symboli, tyyppi="Purchase", amount="$100,001 - $250,000", paivia_sitten=5
):
    pvm = (date.today() - timedelta(days=paivia_sitten)).isoformat()
    return {
        "symbol": symboli,
        "senateID": "M000001",
        "disclosureDate": pvm,
        "transactionDate": pvm,
        "firstName": "Testi",
        "lastName": "Henkilo",
        "office": "Testi Henkilo",
        "district": "TX",
        "owner": "",
        "assetDescription": f"{symboli} Inc",
        "assetType": "Stock",
        "type": tyyppi,
        "amount": amount,
        "comment": "",
        "link": "https://example.com",
    }


def _sisapiirirvi(
    symboli, suunta="P-Purchase", maara=2000, hinta=30.0, paivia_sitten=5
):
    pvm = (date.today() - timedelta(days=paivia_sitten)).isoformat()
    return {
        "symbol": symboli,
        "filingDate": pvm,
        "transactionDate": pvm,
        "reportingCik": "0000000001",
        "companyCik": "0000000002",
        "transactionType": suunta,
        "securitiesOwned": 10000,
        "reportingName": "Testi Henkilo",
        "typeOfOwner": "officer",
        "acquisitionOrDisposition": "A" if suunta.startswith("P-") else "D",
        "directOrIndirect": "D",
        "formType": "4",
        "securitiesTransacted": maara,
        "price": hinta,
        "securityName": "Common Stock",
        "url": "https://example.com",
    }


def _tavoitehintarivi(
    symboli, tavoite=120.0, nykyhinta=100.0, paivia_sitten=2, yhtio="Citigroup"
):
    pvm = datetime.now(timezone.utc) - timedelta(days=paivia_sitten)
    return {
        "symbol": symboli,
        "publishedDate": pvm.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "newsURL": "https://example.com",
        "newsTitle": "esimerkkiotsikko",
        "analystName": "Testi Analyytikko",
        "priceTarget": tavoite,
        "adjPriceTarget": tavoite,
        "priceWhenPosted": nykyhinta,
        "newsPublisher": "TheFly",
        "newsBaseURL": "example.com",
        "analystCompany": yhtio,
    }


# --- build_candidates: puhdas logiikka, ei verkkoa eika tietokantaa ---


def test_yhteisesiintyma_saa_korkeimmat_pisteet():
    analyytikko = [
        _analyytikkorivi("AAPL"),
        _analyytikkorivi("MSFT", uusi="Hold", edellinen="Hold", action="hold"),
    ]
    kongressi = [
        _kongressirivi("AAPL"),
        _kongressirivi("TSLA"),
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, kongressi, [], [], [], US_SYMBOLIT
    )
    symbolit = {e["symbol"] for e in ehdokkaat}

    assert {"AAPL", "MSFT", "TSLA"}.issubset(symbolit)

    aapl = next(e for e in ehdokkaat if e["symbol"] == "AAPL")
    assert aapl["source"] == "molemmat"
    assert ehdokkaat[0]["symbol"] == "AAPL"


def test_kelvoton_symboli_suodattuu_pois():
    analyytikko = [
        _analyytikkorivi(""),
        _analyytikkorivi("TOOLONG"),
        _analyytikkorivi("BRK.B"),
        _analyytikkorivi("AAPL"),
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], [], [], US_SYMBOLIT
    )

    assert {e["symbol"] for e in ehdokkaat} == {"AAPL"}


def test_kongressimyynti_suodattuu_pois():
    kongressi = [
        _kongressirivi("AAPL", tyyppi="Purchase"),
        _kongressirivi("MSFT", tyyppi="Sale"),
        _kongressirivi("TSLA", tyyppi="Sale (Partial)"),
        _kongressirivi("NVDA", tyyppi="Exchange"),
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(
        [], kongressi, [], [], [], US_SYMBOLIT
    )

    assert {e["symbol"] for e in ehdokkaat} == {"AAPL"}


def test_sisapiirimyynti_suodattuu_pois_osto_ei():
    sisapiiri = [
        _sisapiirirvi("AAPL", suunta="P-Purchase"),
        _sisapiirirvi("MSFT", suunta="S-Sale"),
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(
        [], [], sisapiiri, [], [], US_SYMBOLIT
    )

    assert {e["symbol"] for e in ehdokkaat} == {"AAPL"}


def test_rikkinainen_data_ei_kaada_funktiota():
    analyytikko = [
        {**_analyytikkorivi("TSLA"), "publishedDate": None},
    ]
    kongressi = [
        _kongressirivi("AAPL", amount="N/A"),
        {**_kongressirivi("MSFT"), "transactionDate": "ei-paivamaara"},
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, kongressi, [], [], [], US_SYMBOLIT
    )

    # Rikkinainen publishedDate hylkaa koko analyytikkorivin (TSLA katoaa
    # kokonaan), mutta rikkinainen amount/paivamaara kongressirivilla vain
    # antaa 0 pistetta kyseisesta kohdasta eika kaada mitaan.
    assert {e["symbol"] for e in ehdokkaat} == {"AAPL", "MSFT"}


def test_sisapiirirvi_rikkinaisella_arvolla_ja_pvmlla_saa_yhden_pisteen():
    rivi = [
        {
            **_sisapiirirvi("AAPL"),
            "securitiesTransacted": None,
            "price": "N/A",
            "transactionDate": "ei-paivamaara",
        }
    ]

    ehdokkaat = trendinmetsastaja.build_candidates([], [], rivi, [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["score"] == 1


def test_tavoitehinta_alle_nykyhinnan_hylataan():
    rivi = [_tavoitehintarivi("AAPL", tavoite=90.0, nykyhinta=100.0)]

    ehdokkaat = trendinmetsastaja.build_candidates([], [], [], rivi, [], US_SYMBOLIT)

    assert ehdokkaat == []


def test_tavoitehinta_pisteytys_kaannetty_u():
    # Kaannetty U: kohtuullinen nousuvara palkitaan, ylisuuri (epauskottava)
    # nousuvara ei.
    plus176 = [_tavoitehintarivi("AAAA", tavoite=27.6, nykyhinta=10.0)]  # +176 %
    plus30 = [_tavoitehintarivi("BBBB", tavoite=130.0, nykyhinta=100.0)]  # +30 %
    plus10 = [_tavoitehintarivi("CCCC", tavoite=110.0, nykyhinta=100.0)]  # +10 %
    plus2 = [_tavoitehintarivi("DDDD", tavoite=102.0, nykyhinta=100.0)]  # +2 %

    e176 = trendinmetsastaja.build_candidates([], [], [], plus176, [], US_SYMBOLIT)
    e30 = trendinmetsastaja.build_candidates([], [], [], plus30, [], US_SYMBOLIT)
    e10 = trendinmetsastaja.build_candidates([], [], [], plus10, [], US_SYMBOLIT)
    e2 = trendinmetsastaja.build_candidates([], [], [], plus2, [], US_SYMBOLIT)

    assert e176[0]["score"] == 1  # yli 60 % - epauskottava, heikko signaali
    assert e30[0]["score"] == 3  # 15-60 % - uskottava, vahvin signaali
    assert e10[0]["score"] == 2  # 5-15 %
    assert e2[0]["score"] == 1  # 0-5 %


def test_matala_osakekurssi_hylkaa_rivin_useasta_lahteesta():
    analyytikko = [_analyytikkorivi("AAAA", hinta=0.40)]
    sisapiiri = [_sisapiirirvi("BBBB", hinta=0.40)]
    tavoitehinta = [_tavoitehintarivi("CCCC", nykyhinta=0.40, tavoite=0.50)]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], sisapiiri, tavoitehinta, [], US_SYMBOLIT
    )

    assert ehdokkaat == []


def test_riittava_osakekurssi_ei_hylkaa_rivia():
    analyytikko = [_analyytikkorivi("AAAA", hinta=45.0)]
    sisapiiri = [_sisapiirirvi("BBBB", hinta=45.0)]
    tavoitehinta = [_tavoitehintarivi("CCCC", nykyhinta=45.0, tavoite=50.0)]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], sisapiiri, tavoitehinta, [], US_SYMBOLIT
    )

    assert {e["symbol"] for e in ehdokkaat} == {"AAAA", "BBBB", "CCCC"}


def test_puuttuva_hinta_paastaa_rivin_lapi():
    analyytikko = [{**_analyytikkorivi("AAAA"), "priceWhenPosted": None}]
    sisapiiri = [{**_sisapiirirvi("BBBB"), "price": None}]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], sisapiiri, [], [], US_SYMBOLIT
    )

    assert {e["symbol"] for e in ehdokkaat} == {"AAAA", "BBBB"}


def test_tavoitehintarivi_rikkinaisilla_hinnoilla_hylataan():
    rivi = [
        {
            **_tavoitehintarivi("AAPL"),
            "priceTarget": "N/A",
            "adjPriceTarget": None,
            "priceWhenPosted": "N/A",
        }
    ]

    ehdokkaat = trendinmetsastaja.build_candidates([], [], [], rivi, [], US_SYMBOLIT)

    assert ehdokkaat == []


def test_yli_14_vrk_tavoitehintarivi_hylataan():
    vanha = [_tavoitehintarivi("AAPL", paivia_sitten=15)]

    ehdokkaat = trendinmetsastaja.build_candidates([], [], [], vanha, [], US_SYMBOLIT)

    assert ehdokkaat == []


def test_ohita_symbolit_suodattuu_pois():
    analyytikko = [_analyytikkorivi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], [], ["AAPL"], US_SYMBOLIT
    )

    assert ehdokkaat == []


def test_symboli_joka_ei_ole_us_symbolit_joukossa_suodattuu_pois():
    analyytikko = [_analyytikkorivi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], [], [], {"MSFT"}
    )

    assert ehdokkaat == []


def test_downgrade_ei_paady_ehdokkaaksi():
    analyytikko = [
        _analyytikkorivi("AAPL", action="downgrade", uusi="Hold", edellinen="Buy")
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], [], [], US_SYMBOLIT
    )

    assert ehdokkaat == []


def test_yli_14_vrk_analyytikkorivi_hylataan_13_vrk_ei():
    vanha = [_analyytikkorivi("AAPL", paivia_sitten=15)]
    tuore = [_analyytikkorivi("AAPL", paivia_sitten=13)]

    assert trendinmetsastaja.build_candidates(vanha, [], [], [], [], US_SYMBOLIT) == []

    ehdokkaat = trendinmetsastaja.build_candidates(tuore, [], [], [], [], US_SYMBOLIT)
    assert {e["symbol"] for e in ehdokkaat} == {"AAPL"}


def test_jarjestys_upgrade_yli_initialise_yli_hold():
    hold_rivi = [_analyytikkorivi("AAPL", uusi="Buy", edellinen="Buy", action="hold")]
    upgrade_rivi = [
        _analyytikkorivi("MSFT", uusi="Buy", edellinen="Neutral", action="upgrade")
    ]
    initialise_rivi = [
        {
            **_analyytikkorivi("TSLA", uusi="Buy", action="initialise"),
            "previousGrade": None,
        }
    ]

    hold_ehdokkaat = trendinmetsastaja.build_candidates(
        hold_rivi, [], [], [], [], US_SYMBOLIT
    )
    upgrade_ehdokkaat = trendinmetsastaja.build_candidates(
        upgrade_rivi, [], [], [], [], US_SYMBOLIT
    )
    initialise_ehdokkaat = trendinmetsastaja.build_candidates(
        initialise_rivi, [], [], [], [], US_SYMBOLIT
    )

    assert hold_ehdokkaat[0]["score"] == 3
    assert initialise_ehdokkaat[0]["score"] == 4
    assert upgrade_ehdokkaat[0]["score"] == 5


def test_initialise_plus_buy_saa_4_pistetta():
    rivi = [
        {
            **_analyytikkorivi("AAPL", uusi="Buy", action="initialise"),
            "previousGrade": None,
        }
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(rivi, [], [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["score"] == 4


def test_initialise_plus_neutral_saa_2_pistetta():
    rivi = [
        {
            **_analyytikkorivi("AAPL", uusi="Neutral", action="initialise"),
            "previousGrade": None,
        }
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(rivi, [], [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["score"] == 2


def test_initialise_perustelu_ei_kayta_previousgradea():
    rivi = [
        {
            **_analyytikkorivi("AAPL", uusi="Buy", action="initialise"),
            "previousGrade": None,
        }
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(rivi, [], [], [], [], US_SYMBOLIT)

    syy = ehdokkaat[0]["reason"]
    assert "aloitti seurannan" in syy
    assert "piti ennallaan" not in syy
    assert "None" not in syy


def test_tuntematon_action_ei_kaada_saa_yhden_pisteen_ja_varoituksen(monkeypatch):
    varoitukset = []
    monkeypatch.setattr(
        trendinmetsastaja._loki, "warning", lambda viesti: varoitukset.append(viesti)
    )

    rivi = [
        _analyytikkorivi("AAPL", uusi="Hold", edellinen="Hold", action="reiterated")
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(rivi, [], [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["score"] == 1
    assert len(varoitukset) == 1
    assert "reiterated" in varoitukset[0]
    assert "AAPL" in varoitukset[0]
    assert "muutti luokitusta" in ehdokkaat[0]["reason"]
    assert "piti ennallaan" not in ehdokkaat[0]["reason"]


def test_kongressirivi_ilman_amountia_ja_tuoretta_pvm_saa_yhden_pisteen():
    rivi = [_kongressirivi("AAPL", amount="N/A", paivia_sitten=90)]

    ehdokkaat = trendinmetsastaja.build_candidates([], rivi, [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["score"] == 1


def test_toistuvuusbonus_on_korkeintaan_3_ja_pohja_on_paras_yksittainen_rivi():
    rivit = [
        _kongressirivi("AAPL", amount="$1,001 - $15,000", paivia_sitten=90)
        for _ in range(10)
    ]
    # Yksi rivi selvasti paremmalla pisteytyksella (tuore, iso summa).
    rivit[3] = _kongressirivi("AAPL", amount="$100,001 - $250,000", paivia_sitten=5)

    ehdokkaat = trendinmetsastaja.build_candidates([], rivit, [], [], [], US_SYMBOLIT)

    assert len(ehdokkaat) == 1
    # Paras yksittainen rivi: osto(1) + amount>=50000(2) + tuore<30vrk(2) = 5
    # Toistuvuusbonus: 10 mainintaa -> 9 ekstraa -> min(3, 9) = 3
    assert ehdokkaat[0]["score"] == 5 + 3


def test_yksi_analyytikko_ja_yksi_kongressirivi_saa_yhteisesiintymabonuksen_mutta_ei_toistuvuutta():  # noqa: E501
    analyytikko = [_analyytikkorivi("AAPL")]
    kongressi = [_kongressirivi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, kongressi, [], [], [], US_SYMBOLIT
    )

    aapl = ehdokkaat[0]
    assert aapl["source"] == "molemmat"
    # analyytikko: upgrade(3) + Buy(2) = 5
    # kongressi: osto(1) + amount 250000(2) + tuore 5vrk(2) = 5
    # yhteisesiintyma +3, toistuvuus 0 (tasan yksi maininta kummassakin)
    assert aapl["score"] == 5 + 5 + 3


def test_symboli_kolmessa_lahteessa_saa_yhteisesiintymabonuksen_tasan_kerran():
    analyytikko = [_analyytikkorivi("AAPL")]
    kongressi = [_kongressirivi("AAPL")]
    sisapiiri = [_sisapiirirvi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, kongressi, sisapiiri, [], [], US_SYMBOLIT
    )

    aapl = ehdokkaat[0]
    assert aapl["source"] == "molemmat"
    # analyytikko 5 + kongressi 5 + sisapiiri
    # (osto(1)+arvo60000>=50000(2)+tuore5vrk(2)=5)
    # yhteisesiintyma +3 TASAN KERRAN (ei kolmesti), toistuvuus 0
    assert aapl["score"] == 5 + 5 + 5 + 3


def test_symboli_vain_yhdessa_lahteessa_ei_saa_bonusta():
    analyytikko = [_analyytikkorivi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], [], [], US_SYMBOLIT
    )

    assert ehdokkaat[0]["source"] == "analyytikko"
    assert ehdokkaat[0]["score"] == 5


def test_bonus_kun_molemmat_lahteet_yltavat_yhteisesiintymakynnykseen():
    analyytikko = [
        _analyytikkorivi("AAPL", uusi="Buy", edellinen="Buy", action="hold")
    ]  # 3p
    tavoitehinta = [
        _tavoitehintarivi("AAPL", tavoite=130.0, nykyhinta=100.0)
    ]  # +30 % -> 3p

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], tavoitehinta, [], US_SYMBOLIT
    )

    aapl = ehdokkaat[0]
    assert aapl["source"] == "molemmat"
    assert aapl["score"] == 3 + 3 + 3  # bonus mukana


def test_ei_bonusta_kun_molemmat_lahteet_alle_kynnyksen_lilak_tapaus():
    # LILAK-tapaus: initialise+Neutral (2p) ja +2 % tavoite (1p) - kumpikaan
    # ei ole aito ostosignaali, joten bonusta ei pida antaa.
    analyytikko = [
        {
            **_analyytikkorivi("LILAK", uusi="Neutral", action="initialise"),
            "previousGrade": None,
        }
    ]
    tavoitehinta = [
        _tavoitehintarivi("LILAK", tavoite=102.0, nykyhinta=100.0)
    ]  # +2 % -> 1p

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], tavoitehinta, [], US_SYMBOLIT
    )

    lilak = ehdokkaat[0]
    assert lilak["source"] == "molemmat"
    assert lilak["score"] == 2 + 1  # EI bonusta


def test_ei_bonusta_kun_vain_yksi_lahde_yltaa_kynnykseen():
    analyytikko = [
        _analyytikkorivi("AAPL", uusi="Buy", edellinen="Buy", action="hold")
    ]  # 3p
    tavoitehinta = [
        _tavoitehintarivi("AAPL", tavoite=102.0, nykyhinta=100.0)
    ]  # +2 % -> 1p

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], tavoitehinta, [], US_SYMBOLIT
    )

    aapl = ehdokkaat[0]
    assert aapl["source"] == "molemmat"
    assert aapl["score"] == 3 + 1  # yksi kelvollinen lahde ei riita bonukseen


def test_kolme_lahdetta_joista_kaksi_yltaa_kynnykseen_saa_bonuksen_kerran():
    analyytikko = [
        _analyytikkorivi("AAPL", uusi="Buy", edellinen="Buy", action="hold")
    ]  # 3p
    tavoitehinta = [
        _tavoitehintarivi("AAPL", tavoite=102.0, nykyhinta=100.0)
    ]  # 1p (heikko)
    sisapiiri = [_sisapiirirvi("AAPL")]  # osto(1)+arvo>=50000(2)+tuore<30vrk(2) = 5p

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], sisapiiri, tavoitehinta, [], US_SYMBOLIT
    )

    aapl = ehdokkaat[0]
    assert aapl["source"] == "molemmat"
    # analyytikko(3) ja sisapiiri(5) yltavat kynnykseen -> bonus kerran, ei
    # kolmesti eika sen mukaan etta lahteita on kolme.
    assert aapl["score"] == 3 + 1 + 5 + 3


def test_kynnyksen_alle_jaava_ehdokas_saa_jatkoon_nolla():
    matala = [
        _analyytikkorivi("AAPL", uusi="Hold", edellinen="Hold", action="hold")
    ]  # 1 piste

    ehdokkaat = trendinmetsastaja.build_candidates(matala, [], [], [], [], US_SYMBOLIT)

    assert len(ehdokkaat) == 1
    assert ehdokkaat[0]["score"] < CANDIDATE_THRESHOLD
    assert ehdokkaat[0]["advanced"] == 0


def test_jos_yksikaan_ei_ylita_kynnysta_jatkoon_on_tyhja():
    matala = [
        _analyytikkorivi("AAAA", uusi="Hold", edellinen="Hold", action="hold"),
        _analyytikkorivi("BBBB", uusi="Hold", edellinen="Hold", action="hold"),
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(matala, [], [], [], [], US_SYMBOLIT)

    assert len(ehdokkaat) == 2
    assert all(e["advanced"] == 0 for e in ehdokkaat)


# --- viiteostohinta: kulkee pisteytyksen rinnalla, ei sen lapi ---


def test_viiteostohinta_analyytikkorivilta():
    rivi = [_analyytikkorivi("AAPL", hinta=123.45)]

    ehdokkaat = trendinmetsastaja.build_candidates(rivi, [], [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["reference_price"] == 123.45


def test_viiteostohinta_tavoitehintarivilta():
    rivi = [_tavoitehintarivi("AAPL", tavoite=150.0, nykyhinta=111.11)]

    ehdokkaat = trendinmetsastaja.build_candidates([], [], [], rivi, [], US_SYMBOLIT)

    assert ehdokkaat[0]["reference_price"] == 111.11


def test_viiteostohinta_puuttuu_pelkasta_kongressirivista():
    rivi = [_kongressirivi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates([], rivi, [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["reference_price"] is None


def test_viiteostohinta_ei_tule_sisapiirilahteesta():
    # Tarkoituksellinen rajaus: _pisteyta_sisapiirirvi():lla ei ole
    # ylaikarajaa (vain ika-perusteiset pisteet), joten sen hinta voi olla
    # mielivaltaisen vanha - huono vertailukohta MAX_PRICE_DRIFTille. Ala
    # "korjaa" tata lisaamatta samalla hinnan ikaa ja skaalattua toleranssia
    # (ks. CLAUDE.md, Agentti 1).
    rivi = [_sisapiirirvi("AAPL", hinta=200.0)]

    ehdokkaat = trendinmetsastaja.build_candidates([], [], rivi, [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["reference_price"] is None


def test_viiteostohinta_valitaan_vahvimmasta_lahteesta():
    analyytikko = [_analyytikkorivi("AAPL", hinta=100.0)]  # upgrade+Buy = 5p
    tavoitehinta = [
        _tavoitehintarivi("AAPL", tavoite=102.0, nykyhinta=200.0)
    ]  # +2 % = 1p

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], tavoitehinta, [], US_SYMBOLIT
    )

    assert ehdokkaat[0]["reference_price"] == 100.0


def test_viiteostohinta_tasapisteissa_lahteet_jarjestys_ratkaisee():
    # analyytikko: hold+Buy = 3p. tavoitehinta: +30 % = 3p. Tasapeli - LAHTEET
    # on ("analyytikko", "kongressi", "sisapiiri", "tavoitehinta"), joten
    # analyytikko voittaa.
    analyytikko = [
        _analyytikkorivi(
            "AAPL", uusi="Buy", edellinen="Buy", action="hold", hinta=111.0
        )
    ]
    tavoitehinta = [_tavoitehintarivi("AAPL", tavoite=130.0, nykyhinta=222.0)]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, [], [], tavoitehinta, [], US_SYMBOLIT
    )

    assert ehdokkaat[0]["reference_price"] == 111.0


def test_viiteostohinta_puuttuu_tai_jasentymaton_ei_vaikuta_pisteisiin():
    normaali = [_analyytikkorivi("AAPL")]
    ehdokkaat_normaali = trendinmetsastaja.build_candidates(
        normaali, [], [], [], [], US_SYMBOLIT
    )
    odotetut_pisteet = ehdokkaat_normaali[0]["score"]

    for rikki_arvo in (None, "ei-numero"):
        rikki = [{**_analyytikkorivi("AAPL"), "priceWhenPosted": rikki_arvo}]
        ehdokkaat_rikki = trendinmetsastaja.build_candidates(
            rikki, [], [], [], [], US_SYMBOLIT
        )
        assert ehdokkaat_rikki[0]["reference_price"] is None
        assert ehdokkaat_rikki[0]["score"] == odotetut_pisteet

    puuttuu = _analyytikkorivi("AAPL")
    del puuttuu["priceWhenPosted"]
    ehdokkaat_puuttuu = trendinmetsastaja.build_candidates(
        [puuttuu], [], [], [], [], US_SYMBOLIT
    )
    assert ehdokkaat_puuttuu[0]["reference_price"] is None
    assert ehdokkaat_puuttuu[0]["score"] == odotetut_pisteet


def test_priceWhenPosted_nolla_tai_negatiivinen_ei_tuota_viiteostohintaa():
    # priceWhenPosted <= 0 hylkaa koko rivin jo MIN_SHARE_PRICE-tarkistuksessa
    # (_hinta_liian_matala), ennen kuin viiteostohintaa edes poimitaan -
    # rivi ei siis paady ehdokkaaksi lainkaan, ei vain ilman hintaa.
    nolla = [_analyytikkorivi("AAAA", hinta=0.0)]
    negatiivinen = [_analyytikkorivi("BBBB", hinta=-10.0)]

    assert trendinmetsastaja.build_candidates(nolla, [], [], [], [], US_SYMBOLIT) == []
    assert (
        trendinmetsastaja.build_candidates(negatiivinen, [], [], [], [], US_SYMBOLIT)
        == []
    )


def test_poimi_hinta_puolustava_kasittely():
    # Suora yksikkotesti _extract_price():lle: 0/negatiivinen hinta ei ole
    # havaittavissa build_candidates():n kautta, koska pisteytysfunktiot
    # hylkaavat koko rivin jo aiemmin samalla kentalla (ks. testi ylla).
    # Tama testaa apufunktion oman puolustavan kasittelyn suoraan.
    poimi = trendinmetsastaja._extract_price
    assert poimi({"priceWhenPosted": 0}, "priceWhenPosted") is None
    assert poimi({"priceWhenPosted": -5}, "priceWhenPosted") is None
    assert poimi({"priceWhenPosted": None}, "priceWhenPosted") is None
    assert poimi({}, "priceWhenPosted") is None
    assert poimi({"priceWhenPosted": "ei-numero"}, "priceWhenPosted") is None
    assert poimi({"priceWhenPosted": "123.45"}, "priceWhenPosted") == 123.45


def test_viiteostohinta_toistuvista_riveista_tulee_parhaasta_rivista():
    rivit = [
        _analyytikkorivi("AAPL", uusi="Neutral", action="hold", hinta=50.0),  # 1p
        _analyytikkorivi(
            "AAPL", uusi="Buy", action="upgrade", hinta=222.0
        ),  # 5p - paras
        _analyytikkorivi("AAPL", uusi="Hold", action="hold", hinta=10.0),  # 1p
    ]

    ehdokkaat = trendinmetsastaja.build_candidates(rivit, [], [], [], [], US_SYMBOLIT)

    assert ehdokkaat[0]["reference_price"] == 222.0


def test_regressio_pisteet_lahde_syy_muuttumattomia_monilahdetapauksessa():
    analyytikko = [_analyytikkorivi("AAPL")]
    kongressi = [_kongressirivi("AAPL")]

    ehdokkaat = trendinmetsastaja.build_candidates(
        analyytikko, kongressi, [], [], [], US_SYMBOLIT
    )

    aapl = ehdokkaat[0]
    assert aapl["source"] == "molemmat"
    assert aapl["score"] == 5 + 5 + 3  # sama kuin ennen viiteostohinta-muutosta
    assert "korotti" in aapl["reason"]
    assert "Kongressiosto" in aapl["reason"]
    # uusi kentta lisatty vanhojen rinnalle, ei niiden tilalle
    assert aapl["reference_price"] == 100.0


# --- aja(): tietokantaan kirjoittava kerros, hakufunktiot monkeypatchattu ---


def test_aja_normaali_yli_valittavat_maaran(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    analyytikko = [
        _analyytikkorivi(s, paivia_sitten=i) for i, s in enumerate(SYMBOLIT_12)
    ]

    monkeypatch.setattr(trendinmetsastaja, "fetch_analyst_changes", lambda: analyytikko)
    monkeypatch.setattr(trendinmetsastaja, "fetch_congress_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_insider_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_price_targets", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: set(SYMBOLIT_12))

    trendinmetsastaja.run("ajo1", "normaali", db_path=polku)

    rivit = tietokanta.get_candidates("ajo1", advanced_only=False, db_path=polku)
    assert len(rivit) == len(SYMBOLIT_12)

    sijoitukset = sorted(rivi["rank"] for rivi in rivit)
    assert sijoitukset == list(range(1, len(SYMBOLIT_12) + 1))

    # Kaikki 12 ehdokasta ylittavat kynnyksen (upgrade+Buy=5), joten
    # jatkoon rajautuu MAX_SELECTED:iin.
    jatkoon_maara = sum(1 for rivi in rivit if rivi["advanced"] == 1)
    assert jatkoon_maara == MAX_SELECTED


def test_aja_normaali_alle_valittavat_maaran(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    symbolit = ["AAAA", "BBBB", "CCCC"]
    analyytikko = [_analyytikkorivi(s) for s in symbolit]

    monkeypatch.setattr(trendinmetsastaja, "fetch_analyst_changes", lambda: analyytikko)
    monkeypatch.setattr(trendinmetsastaja, "fetch_congress_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_insider_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_price_targets", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: set(symbolit))

    trendinmetsastaja.run("ajo2", "normaali", db_path=polku)

    rivit = tietokanta.get_candidates("ajo2", advanced_only=False, db_path=polku)
    assert len(rivit) == 3
    assert all(rivi["advanced"] == 1 for rivi in rivit)


def test_kynnyksen_alle_jaava_ehdokas_tallentuu_mutta_ei_jatkoon(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    matala = [
        _analyytikkorivi("AAAA", uusi="Hold", edellinen="Hold", action="hold")
    ]  # 1 piste

    monkeypatch.setattr(trendinmetsastaja, "fetch_analyst_changes", lambda: matala)
    monkeypatch.setattr(trendinmetsastaja, "fetch_congress_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_insider_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_price_targets", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: {"AAAA"})

    trendinmetsastaja.run("ajoK", "normaali", db_path=polku)

    rivit = tietokanta.get_candidates("ajoK", advanced_only=False, db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["advanced"] == 0


def test_pisteet_tallentuvat_tietokantaan(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    analyytikko = [_analyytikkorivi("AAPL")]  # upgrade(3) + Buy(2) = 5 pistetta

    monkeypatch.setattr(trendinmetsastaja, "fetch_analyst_changes", lambda: analyytikko)
    monkeypatch.setattr(trendinmetsastaja, "fetch_congress_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_insider_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_price_targets", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: {"AAPL"})

    trendinmetsastaja.run("ajoP", "normaali", db_path=polku)

    rivit = tietokanta.get_candidates("ajoP", advanced_only=False, db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["score"] == 5


def test_aja_normaali_keskeytyy_jos_us_symbolit_haku_epaonnistuu(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    monkeypatch.setattr(
        trendinmetsastaja,
        "fetch_analyst_changes",
        lambda: [_analyytikkorivi("AAPL")],
    )
    monkeypatch.setattr(trendinmetsastaja, "fetch_congress_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_insider_trades", lambda: [])
    monkeypatch.setattr(trendinmetsastaja, "fetch_price_targets", lambda: [])

    def _epaonnistu():
        raise RuntimeError("Twelve Data /stocks ei vastaa")

    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: _epaonnistu())

    with pytest.raises(RuntimeError):
        trendinmetsastaja.run("ajo4", "normaali", db_path=polku)

    rivit = tietokanta.get_candidates("ajo4", advanced_only=False, db_path=polku)
    assert rivit == []


def test_aja_tarkistus_ei_kutsu_hakufunktioita(tmp_path, monkeypatch):
    polku = tmp_path / "testi.db"
    tietokanta.init_database(polku)

    yhteys = tietokanta.open_connection(polku)
    try:
        yhteys.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, buy_date, buy_run_id
            ) VALUES ('AAPL', 'aktiivinen', 100, 10, 1000, '2026-01-01', 'ajo0')
            """
        )
        yhteys.commit()
    finally:
        yhteys.close()

    def _kaadu():
        raise AssertionError("hakufunktiota ei saa kutsua tarkistusajossa")

    monkeypatch.setattr(trendinmetsastaja, "fetch_analyst_changes", lambda: _kaadu())
    monkeypatch.setattr(trendinmetsastaja, "fetch_congress_trades", lambda: _kaadu())
    monkeypatch.setattr(trendinmetsastaja, "fetch_insider_trades", lambda: _kaadu())
    monkeypatch.setattr(trendinmetsastaja, "fetch_price_targets", lambda: _kaadu())
    monkeypatch.setattr(trendinmetsastaja, "fetch_us_symbols", lambda: _kaadu())

    trendinmetsastaja.run("ajo3", "tarkistus", db_path=polku)

    rivit = tietokanta.get_candidates("ajo3", advanced_only=False, db_path=polku)
    assert len(rivit) == 1
    assert rivit[0]["symbol"] == "AAPL"
    assert rivit[0]["advanced"] == 1


# --- fetch_us_symbols(): 30 vrk:n valimuisti ja epaonnistumisen varajarjestelma ---


def _kirjoita_symbolilista_valimuistiin(valimuisti_dir, symbolit, paivia_sitten):
    valimuisti_dir.mkdir(parents=True, exist_ok=True)
    pvm = (date.today() - timedelta(days=paivia_sitten)).isoformat()
    tiedosto = valimuisti_dir / f"twelvedata-stocks_{pvm}.json"
    tiedosto.write_text(json.dumps(sorted(symbolit)), encoding="utf-8")


def test_symbolilista_tuore_valimuisti_kaytetaan_ilman_verkkokutsua(
    tmp_path, monkeypatch
):
    valimuisti_dir = tmp_path / "valimuisti"
    monkeypatch.setattr(trendinmetsastaja, "VALIMUISTI_DIR", valimuisti_dir)
    monkeypatch.setattr(trendinmetsastaja, "get_twelve_data_key", lambda: "testiavain")
    _kirjoita_symbolilista_valimuistiin(
        valimuisti_dir, {"AAPL", "MSFT"}, paivia_sitten=5
    )

    def _ei_saa_kutsua(*args, **kwargs):
        raise AssertionError(
            "verkkokutsua ei saa tehda kun 30 vrk:n valimuisti on tuore"
        )

    monkeypatch.setattr(trendinmetsastaja.requests, "get", _ei_saa_kutsua)

    symbolit = trendinmetsastaja.fetch_us_symbols()

    assert symbolit == {"AAPL", "MSFT"}


def test_symbolilista_haku_epaonnistuu_eika_valimuistia_ole_keskeytyy(
    tmp_path, monkeypatch
):
    valimuisti_dir = tmp_path / "valimuisti"
    monkeypatch.setattr(trendinmetsastaja, "VALIMUISTI_DIR", valimuisti_dir)
    monkeypatch.setattr(trendinmetsastaja, "get_twelve_data_key", lambda: "testiavain")

    def _kaadu(*args, **kwargs):
        raise requests.exceptions.Timeout("simuloitu aikakatkaisu")

    monkeypatch.setattr(trendinmetsastaja.requests, "get", _kaadu)

    with pytest.raises(RuntimeError):
        trendinmetsastaja.fetch_us_symbols()


def test_symbolilista_40_vrk_vanhaa_ei_kayteta_varajarjestelmana(tmp_path, monkeypatch):
    valimuisti_dir = tmp_path / "valimuisti"
    monkeypatch.setattr(trendinmetsastaja, "VALIMUISTI_DIR", valimuisti_dir)
    monkeypatch.setattr(trendinmetsastaja, "get_twelve_data_key", lambda: "testiavain")
    _kirjoita_symbolilista_valimuistiin(valimuisti_dir, {"AAPL"}, paivia_sitten=40)

    def _kaadu(*args, **kwargs):
        raise requests.exceptions.Timeout("simuloitu aikakatkaisu")

    monkeypatch.setattr(trendinmetsastaja.requests, "get", _kaadu)

    with pytest.raises(RuntimeError):
        trendinmetsastaja.fetch_us_symbols()
