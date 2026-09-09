"""Testit dashboard/nakyma.py:n puhtaalle apufunktiolle _tyhjat_viivoiksi().

nakyma.py suorittaa moduulitasolla Streamlit- ja tietokantakutsuja (sivun
piirto), joten sita ei voi tuoda tavallisella importilla testeissa ilman
oikeaa Streamlit-kontekstia ja tuotantokantaa. _tyhjat_viivoiksi()-funktio
poimitaan siksi tiedoston AST:sta ja ajetaan omassa nimiavaruudessaan -
tama ei suorita mitaan muuta tiedoston sisallosta eika kosketa oikeaa
nakyma.py:ta millaan tavalla.
"""

import ast
from pathlib import Path

import pandas as pd

NAKYMA_POLKU = Path(__file__).resolve().parent.parent / "dashboard" / "nakyma.py"


def _lataa_tyhjat_viivoiksi():
    lahde = NAKYMA_POLKU.read_text(encoding="utf-8")
    puu = ast.parse(lahde, filename=str(NAKYMA_POLKU))
    funktio = next(
        solmu
        for solmu in puu.body
        if isinstance(solmu, ast.FunctionDef) and solmu.name == "_tyhjat_viivoiksi"
    )
    moduuli = ast.Module(body=[funktio], type_ignores=[])
    ast.fix_missing_locations(moduuli)
    nimiavaruus = {"pd": pd}
    exec(compile(moduuli, str(NAKYMA_POLKU), "exec"), nimiavaruus)
    return nimiavaruus["_tyhjat_viivoiksi"]


_tyhjat_viivoiksi = _lataa_tyhjat_viivoiksi()


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
