"""Testit dashboard/nakyma.py:n puhtaille apufunktioille.

nakyma.py suorittaa moduulitasolla Streamlit- ja tietokantakutsuja (sivun
piirto), joten sita ei voi tuoda tavallisella importilla testeissa ilman
oikeaa Streamlit-kontekstia ja tuotantokantaa. Apufunktiot poimitaan siksi
tiedoston AST:sta ja ajetaan omassa nimiavaruudessaan - tama ei suorita
mitaan muuta tiedoston sisallosta eika kosketa oikeaa nakyma.py:ta millaan
tavalla.
"""

import ast
import json
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import pytest

NAKYMA_POLKU = Path(__file__).resolve().parent.parent / "dashboard" / "nakyma.py"


def _lataa_funktiot(*nimet):
    """Poimii nimetyt funktiot AST:sta yhteen nimiavaruuteen.

    Yhteinen nimiavaruus, koska apufunktiot kutsuvat toisiaan (esim.
    _tuottokaavio kayttaa _suomi_pvm:aa). Palauttaa funktiot samassa
    jarjestyksessa kuin nimet annettiin.
    """
    lahde = NAKYMA_POLKU.read_text(encoding="utf-8")
    puu = ast.parse(lahde, filename=str(NAKYMA_POLKU))
    loydetyt = {
        solmu.name: solmu
        for solmu in puu.body
        if isinstance(solmu, ast.FunctionDef) and solmu.name in nimet
    }
    puuttuvat = [nimi for nimi in nimet if nimi not in loydetyt]
    if puuttuvat:
        raise LookupError(f"funktioita ei loydy nakyma.py:sta: {puuttuvat}")
    moduuli = ast.Module(body=[loydetyt[nimi] for nimi in nimet], type_ignores=[])
    ast.fix_missing_locations(moduuli)
    nimiavaruus = {"pd": pd, "alt": alt, "date": date}
    exec(compile(moduuli, str(NAKYMA_POLKU), "exec"), nimiavaruus)
    return tuple(nimiavaruus[nimi] for nimi in nimet)


(
    _tyhjat_viivoiksi,
    _suomi_pvm,
    _muotoile_tuotto,
    _tuottosarja,
    _tuottokaavio,
    _tarkistus_vanhentunut,
    _suomi_aika,
    _kuukauden_nimi,
    _kuukaudet,
    _tapahtumataulukko,
    _kuukauden_yhteenveto,
    _kielto_ohitettu,
    _pisteiden_tila,
) = _lataa_funktiot(
    "_tyhjat_viivoiksi",
    "_suomi_pvm",
    "_muotoile_tuotto",
    "_tuottosarja",
    "_tuottokaavio",
    "_tarkistus_vanhentunut",
    "_suomi_aika",
    "_kuukauden_nimi",
    "_kuukaudet",
    "_tapahtumataulukko",
    "_kuukauden_yhteenveto",
    "_kielto_ohitettu",
    "_pisteiden_tila",
)


def test_none_korvautuu_viivalla():
    df = pd.DataFrame({"sarake": [1.0, None, 3.0]})

    tulos = _tyhjat_viivoiksi(df)

    assert list(tulos["sarake"]) == [1.0, "-", 3.0]


def test_nan_korvautuu_viivalla():
    df = pd.DataFrame({"sarake": [1.0, float("nan"), 3.0]})

    tulos = _tyhjat_viivoiksi(df)

    assert list(tulos["sarake"]) == [1.0, "-", 3.0]


def test_nolla_tyhja_merkkijono_ja_false_eivat_korvaudu():
    # Tarkein kohta: 0 on taysin kelvollinen pistemaara, ei tyhja arvo.
    df = pd.DataFrame(
        {
            "pisteet": [0, 5, None],
            "teksti": ["", "jotain", None],
            "lippu": [False, True, None],
        }
    )

    tulos = _tyhjat_viivoiksi(df)

    assert list(tulos["pisteet"]) == [0, 5, "-"]
    assert list(tulos["teksti"]) == ["", "jotain", "-"]
    assert list(tulos["lippu"]) == [False, True, "-"]


def test_alkuperainen_dataframe_ei_muutu():
    df = pd.DataFrame({"sarake": [1.0, None, 3.0]})

    _tyhjat_viivoiksi(df)

    assert df["sarake"].isna().sum() == 1


def test_tyhja_dataframe_ei_kaada_funktiota():
    df = pd.DataFrame()

    tulos = _tyhjat_viivoiksi(df)

    assert tulos.empty


def test_sarake_ilman_tyhjia_pysyy_numeerisena():
    # Koskematon sarake ei saa muuttua tekstiksi - muuten lajittelu
    # menisi aakkosjarjestykseen (10 ennen 9).
    df = pd.DataFrame({"luku": [1, 2, 10]})

    tulos = _tyhjat_viivoiksi(df)

    assert pd.api.types.is_numeric_dtype(tulos["luku"])
    assert list(tulos["luku"]) == [1, 2, 10]


# --- Vaihe 5d: salkun tuotto ---

_SARAKKEET = ["pvm", "salkku_pct", "vertailu_pct", "ero_pct"]


def _kuva(pvm, total_value, benchmark_price):
    return {
        "snapshot_date": pvm,
        "total_value": total_value,
        "benchmark_price": benchmark_price,
    }


# Tilannekuvat kannasta 7.9.-30.9.2026 (9.9. kolme tarkistusajoa samana paivana).
_OIKEA_DATA = [
    _kuva("2026-09-07", 10000.0, 770.19),
    _kuva("2026-09-09", 9968.181175747599, 765.96002),
    _kuva("2026-09-09", 9968.181175747599, 765.96002),
    _kuva("2026-09-09", 9968.181175747599, 765.96002),
    _kuva("2026-09-10", 9956.488393167047, 762.40002),
    _kuva("2026-09-14", 9986.044779841397, 764.28998),
    _kuva("2026-09-17", 9846.9680372564, 754.049988),
    _kuva("2026-09-21", 9859.215759457566, 761.69),
    _kuva("2026-09-25", 9825.68018577857, 767.17999),
    _kuva("2026-09-29", 9880.082327689928, 765.60999),
    _kuva("2026-09-30", 9837.50816399284, 764.20001),
]


@pytest.mark.parametrize(
    "iso, odotus",
    [
        ("2026-09-07", "7.9.2026"),
        ("2026-10-02", "2.10.2026"),
        ("2026-12-31", "31.12.2026"),
    ],
)
def test_suomi_pvm(iso, odotus):
    assert _suomi_pvm(iso) == odotus


@pytest.mark.parametrize(
    "argumentit, odotus",
    [
        ((-1.6249,), "-1.62 %"),
        ((2.5,), "+2.50 %"),
        ((0.0,), "+0.00 %"),
        ((-0.8472, "%-yks"), "-0.85 %-yks"),
        ((None,), "-"),
        ((float("nan"),), "-"),
    ],
)
def test_muotoile_tuotto(argumentit, odotus):
    assert _muotoile_tuotto(*argumentit) == odotus


def test_tuottosarja_tyhja_lista_antaa_tyhjan():
    sarja = _tuottosarja([])

    assert sarja.empty
    assert list(sarja.columns) == _SARAKKEET


def test_tuottosarja_ilman_vertailukurssia_tyhja():
    sarja = _tuottosarja(
        [_kuva("2026-09-07", 10000.0, None), _kuva("2026-09-08", 10100.0, None)]
    )

    assert sarja.empty
    assert list(sarja.columns) == _SARAKKEET


def test_lahtopiste_ohittaa_vertailukurssittomat_alusta():
    sarja = _tuottosarja(
        [
            _kuva("2026-09-06", 9000.0, None),
            _kuva("2026-09-07", 10000.0, 100.0),
            _kuva("2026-09-08", 10500.0, 110.0),
        ]
    )

    assert list(sarja["pvm"]) == ["2026-09-07", "2026-09-08"]
    assert sarja.iloc[0]["salkku_pct"] == pytest.approx(0.0)
    assert sarja.iloc[0]["vertailu_pct"] == pytest.approx(0.0)
    assert sarja.iloc[1]["salkku_pct"] == pytest.approx(5.0)
    assert sarja.iloc[1]["vertailu_pct"] == pytest.approx(10.0)
    assert sarja.iloc[1]["ero_pct"] == pytest.approx(-5.0)


def test_samalta_paivalta_kaytetaan_viimeisinta():
    sarja = _tuottosarja(
        [
            _kuva("2026-09-07", 10000.0, 100.0),
            _kuva("2026-09-09", 10100.0, 101.0),
            _kuva("2026-09-09", 10300.0, 102.0),
        ]
    )

    assert list(sarja["pvm"]) == ["2026-09-07", "2026-09-09"]
    assert sarja.iloc[1]["salkku_pct"] == pytest.approx(3.0)
    assert sarja.iloc[1]["vertailu_pct"] == pytest.approx(2.0)
    assert sarja.iloc[1]["ero_pct"] == pytest.approx(1.0)


def test_puuttuva_vertailukurssi_myohemmin_jattaa_vertailun_tyhjaksi():
    sarja = _tuottosarja(
        [_kuva("2026-09-07", 10000.0, 100.0), _kuva("2026-09-09", 9900.0, None)]
    )

    toinen = sarja.iloc[1]
    assert toinen["salkku_pct"] == pytest.approx(-1.0)
    assert pd.isna(toinen["vertailu_pct"])
    assert pd.isna(toinen["ero_pct"])


def test_tuottosarja_oikea_data_kiinteat_odotusarvot():
    # Viimeinen rivi = 30.9. tarkistusajon lokirivi
    # "tuotto -1.62 % | SPY -0.78 % | ero -0.85 %-yks". Jos tama hajoaa,
    # etsi virhe toteutuksesta, ala muuta odotusarvoja.
    sarja = _tuottosarja(_OIKEA_DATA)

    def pyoristetty(rivi):
        return (
            round(rivi["salkku_pct"], 2),
            round(rivi["vertailu_pct"], 2),
            round(rivi["ero_pct"], 2),
        )

    assert len(sarja) == 9
    assert pyoristetty(sarja.iloc[0]) == (0.0, 0.0, 0.0)
    assert sarja.iloc[1]["pvm"] == "2026-09-09"
    assert pyoristetty(sarja.iloc[1]) == (-0.32, -0.55, 0.23)
    assert sarja.iloc[-1]["pvm"] == "2026-09-30"
    assert pyoristetty(sarja.iloc[-1]) == (-1.62, -0.78, -0.85)


def test_tuottokaavio_validoituu_ja_sisaltaa_vareja_ja_otsikoita():
    speksi = _tuottokaavio(_tuottosarja(_OIKEA_DATA), "SPY").to_dict()

    teksti = json.dumps(speksi, ensure_ascii=False)
    for odotettu in ("#2a78d6", "#d95926", "Salkku", "SPY", "Päivä"):
        assert odotettu in teksti


def test_puuttuva_vertailukurssi_ei_tuota_tyhjaa_pistetta():
    sarja = _tuottosarja(
        [
            _kuva("2026-09-07", 10000.0, 100.0),
            _kuva("2026-09-09", 9900.0, None),
            _kuva("2026-09-10", 10100.0, 101.0),
        ]
    )

    speksi = _tuottokaavio(sarja, "SPY").to_dict()

    tuottorivit = [
        rivi
        for rivit in speksi["datasets"].values()
        for rivi in rivit
        if "tuotto" in rivi
    ]
    assert tuottorivit  # varmistus etta tarkistus oikeasti kattaa jotain
    assert all(rivi["tuotto"] is not None for rivi in tuottorivit)


# --- Vaihe 6a: vanhentumisvaroitus ---


def test_tarkistus_vanhentunut_ei_yhtaan_tarkistusta():
    varoitus = _tarkistus_vanhentunut(None, date(2026, 10, 6))

    assert varoitus is not None
    assert "Onnistunutta tarkistusajoa ei ole vielä" in varoitus


def test_tarkistus_vanhentunut_lauantaista_tiistaihin_ei_varoitusta():
    # 3 paivaa on normaali vali (la -> ti).
    assert _tarkistus_vanhentunut("2026-10-03T10:00:05", date(2026, 10, 6)) is None


def test_tarkistus_vanhentunut_lauantaista_keskiviikkoon_varoitus():
    varoitus = _tarkistus_vanhentunut("2026-10-03T10:00:05", date(2026, 10, 7))

    assert varoitus is not None
    assert "4 päivää" in varoitus


def test_tarkistus_vanhentunut_sama_paiva_ei_varoitusta():
    assert _tarkistus_vanhentunut("2026-10-06T10:00:05", date(2026, 10, 6)) is None


# --- Vaihe 5e: tapahtumaloki ja "Parhaat pisteet nyt" ---


@pytest.mark.parametrize(
    ("aikaleima", "odotus"),
    [
        ("2026-11-03T15:39:25", "3.11.2026 klo 15.39"),
        ("2026-09-07T08:05:00", "7.9.2026 klo 8.05"),
        ("2026-10-31", "31.10.2026"),
    ],
)
def test_suomi_aika(aikaleima, odotus):
    assert _suomi_aika(aikaleima) == odotus


@pytest.mark.parametrize(
    ("kuukausi", "odotus"),
    [
        ("2026-11", "Marraskuu 2026"),
        ("2027-01", "Tammikuu 2027"),
        ("2026-07", "Heinäkuu 2026"),
    ],
)
def test_kuukauden_nimi(kuukausi, odotus):
    assert _kuukauden_nimi(kuukausi) == odotus


def _tapahtuma(
    aika,
    symboli,
    laji,
    hinta,
    maara,
    summa,
    tulos=None,
    tulos_pct=None,
    perustelu="peruste",
):
    return {
        "time": aika,
        "symbol": symboli,
        "kind": laji,
        "price": hinta,
        "quantity": maara,
        "amount_usd": summa,
        "pnl_usd": tulos,
        "pnl_pct": tulos_pct,
        "rationale": perustelu,
    }


PG_OSTO = _tapahtuma("2026-11-06T12:44:21", "PG", "osto", 167.1, 5.984440, 1000.0)
NVDA_MYYNTI = _tapahtuma(
    "2026-10-31T11:25:45",
    "NVDA",
    "myynti",
    204.64,
    4.629630,
    947.41,
    tulos=-52.59,
    tulos_pct=-5.259259,
)
APH_OSTO = _tapahtuma("2026-09-14T10:00:00", "APH", "osto", 83.92, 11.916110, 1000.0)


def test_kuukaudet_ensimmaisesta_tahan_kuuhun_uusin_ensin():
    kuukaudet = _kuukaudet([PG_OSTO, NVDA_MYYNTI, APH_OSTO], date(2026, 11, 11))

    assert kuukaudet == ["2026-11", "2026-10", "2026-09"]


def test_kuukaudet_ilman_tapahtumia_vain_kuluva_kuukausi():
    assert _kuukaudet([], date(2026, 11, 11)) == ["2026-11"]


def test_kuukaudet_kuluva_kuukausi_mukana_vaikka_tyhja():
    assert _kuukaudet([APH_OSTO], date(2026, 11, 11)) == [
        "2026-11",
        "2026-10",
        "2026-09",
    ]


def test_kuukaudet_vuodenvaihteen_yli():
    joulukuu = _tapahtuma("2026-12-15T10:00:00", "KO", "osto", 87.0, 11.5, 1000.0)

    assert _kuukaudet([joulukuu], date(2027, 2, 1)) == ["2027-02", "2027-01", "2026-12"]


def test_tapahtumataulukko_rajaa_kuukauteen_ja_muotoilee_myynnin():
    df = _tapahtumataulukko([PG_OSTO, NVDA_MYYNTI, APH_OSTO], "2026-10")

    assert df.to_dict("records") == [
        {
            "Aika": "31.10.2026 klo 11.25",
            "Symboli": "NVDA",
            "Tapahtuma": "Myynti",
            "Hinta (USD)": "204.64",
            "Määrä": "4.6296",
            "Summa (USD)": "947.41",
            "Tulos (USD)": "-52.59",
            "Tulos (%)": "-5.26 %",
            "Perustelu": "peruste",
        }
    ]


def test_tapahtumataulukko_ostolla_tulos_on_viiva():
    df = _tapahtumataulukko([PG_OSTO, NVDA_MYYNTI], "2026-11")

    assert list(df["Symboli"]) == ["PG"]
    assert df.iloc[0]["Tapahtuma"] == "Osto"
    assert df.iloc[0]["Tulos (USD)"] == "-"
    assert df.iloc[0]["Tulos (%)"] == "-"
    assert df.iloc[0]["Summa (USD)"] == "1000.00"


def test_tapahtumataulukko_puuttuva_perustelu_viivaksi():
    ilman = _tapahtuma(
        "2026-11-03T15:39:25", "SIBN", "osto", 20.22, 49.456, 1000.0, perustelu=None
    )

    df = _tapahtumataulukko([ilman], "2026-11")

    assert df.iloc[0]["Perustelu"] == "-"


def test_tapahtumataulukko_tyhja_kuukausi_antaa_tyhjan_taulukon_samoilla_sarakkeilla():
    tyhja = _tapahtumataulukko([PG_OSTO], "2026-08")
    taysi = _tapahtumataulukko([PG_OSTO], "2026-11")

    assert tyhja.empty
    assert list(tyhja.columns) == list(taysi.columns)


def test_tapahtumataulukko_sailyttaa_jarjestyksen():
    myohempi = _tapahtuma("2026-11-06T12:44:21", "PG", "osto", 167.1, 5.98, 1000.0)
    aiempi = _tapahtuma("2026-11-03T15:39:25", "SIBN", "osto", 20.22, 49.46, 1000.0)

    df = _tapahtumataulukko([myohempi, aiempi], "2026-11")

    assert list(df["Symboli"]) == ["PG", "SIBN"]


def test_kuukauden_yhteenveto_monikko_ja_nolla_myyntia():
    sibn = _tapahtuma("2026-11-03T15:39:25", "SIBN", "osto", 20.22, 49.46, 1000.0)

    teksti = _kuukauden_yhteenveto([PG_OSTO, sibn, NVDA_MYYNTI], "2026-11")

    assert teksti == "2 ostoa · 0 myyntiä · toteutunut tulos +0.00 USD"


def test_kuukauden_yhteenveto_yksikko_ja_toteutunut_tulos():
    nvda_osto = _tapahtuma("2026-10-27T13:14:38", "NVDA", "osto", 216.0, 4.63, 1000.0)

    teksti = _kuukauden_yhteenveto([NVDA_MYYNTI, nvda_osto], "2026-10")

    assert teksti == "1 osto · 1 myynti · toteutunut tulos -52.59 USD"


KIELTO = {"until": "2026-11-30", "sold_on": "2026-10-31"}


@pytest.mark.parametrize(
    "pisteytys",
    [
        {"ema_fast": None, "ema_slow": None, "ema_cross_date": None},
        {"ema_fast": 205.9, "ema_slow": 212.9, "ema_cross_date": "2026-11-04"},
        {"ema_fast": 220.0, "ema_slow": 215.0, "ema_cross_date": None},
        {"ema_fast": 220.0, "ema_slow": 215.0, "ema_cross_date": "2026-04-14"},
        {"ema_fast": 220.0, "ema_slow": 215.0, "ema_cross_date": "2026-10-31"},
        {"ema_fast": 220.0, "ema_slow": 215.0, "ema_cross_date": "2026-11-12"},
        {"ema_fast": 215.0, "ema_slow": 215.0, "ema_cross_date": "2026-11-12"},
    ],
)
def test_kielto_ohitettu_vastaa_agentti3_saantoa(pisteytys):
    # Tuonti testin sisalla: agents-paketin tuonti alustaa lokituksen, ja
    # conftestin fixturet ohjaavat sen tmp_path:iin vasta testin aikana.
    from agents import paatoksentekija

    odotus, _ = paatoksentekija._check_cooldown_bypass(pisteytys, KIELTO)

    assert _kielto_ohitettu(pisteytys, KIELTO) is odotus


def _tila(
    symboli="CHCO",
    pisteet=8.4,
    kielto=None,
    ohitettu=False,
    *,
    omistetut=frozenset(),
    taynna=False,
    kateista=True,
):
    return _pisteiden_tila(
        symboli,
        pisteet,
        kielto,
        ohitettu,
        omistetut=set(omistetut),
        ostoraja=7,
        salkku_taynna=taynna,
        kateinen_riittaa=kateista,
    )


def test_pisteiden_tila_omistus_menee_kaiken_edelle():
    assert _tila("FITB", 8.8, KIELTO, omistetut={"FITB"}, kateista=False) == (
        "Omistuksessa"
    )


def test_pisteiden_tila_kielto_paattymispaivineen():
    assert _tila("NVDA", 9.1, KIELTO) == "Kielto 30.11.2026 asti"


def test_pisteiden_tila_ohitettu_kielto_ei_esta():
    assert _tila("NVDA", 8.0, KIELTO, ohitettu=True) == "Ostokelpoinen, kielto ohitettu"


def test_pisteiden_tila_ohitettu_kielto_ei_ohita_ostorajaa():
    assert _tila("NVDA", 6.0, KIELTO, ohitettu=True) == "Alle ostorajan"


def test_pisteiden_tila_alle_ostorajan():
    assert _tila(pisteet=6.9) == "Alle ostorajan"


def test_pisteiden_tila_ostoraja_sisaltyy():
    assert _tila(pisteet=7.0) == "Ostokelpoinen"


def test_pisteiden_tila_kateinen_ei_riita():
    # 11.11.2026: CHCO 8,4 hylattiin, koska kateista oli 216,81 USD.
    assert _tila(kateista=False) == "Käteinen ei riitä"


def test_pisteiden_tila_salkku_taynna_ennen_kateista():
    assert _tila(taynna=True, kateista=False) == "Salkku täynnä"
