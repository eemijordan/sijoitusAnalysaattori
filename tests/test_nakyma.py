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
) = _lataa_funktiot(
    "_tyhjat_viivoiksi",
    "_suomi_pvm",
    "_muotoile_tuotto",
    "_tuottosarja",
    "_tuottokaavio",
    "_tarkistus_vanhentunut",
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
