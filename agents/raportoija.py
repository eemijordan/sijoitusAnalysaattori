"""Agentti 4: kokoaa ajon yhteenvedon lokiin (runko, Excel tulossa myöhemmin)."""

from datetime import date

from core import loki, tietokanta
from core.asetukset import BENCHMARK_SYMBOL

_loki = loki.get_logger(__name__)


def _format_usd(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _resolve_position_price(
    position: dict, scores_by_symbol: dict, db_path
) -> tuple[float, bool]:
    """Palauttaa (hinta, oliko_tuore) yhdelle avoimelle positiolle.

    Ensisijainen lahde on taman ajon oma pisteytys. Jos sita ei ole, kaytetaan
    symbolin viimeisinta aiempaa pisteytyshintaa (tietokanta.get_latest_score_
    for_symbol) - ja jos sitakaan ei ole, buy_price:a. Jalkimmaiset kaksi
    tapausta merkitaan "ei tuoreeksi", jotta kutsuja voi laskea kuinka moni
    positio arvostettiin vanhentuneella tiedolla.
    """
    symbol = position["symbol"]
    fresh = scores_by_symbol.get(symbol)
    if fresh is not None and fresh.get("price") is not None:
        return fresh["price"], True

    latest = tietokanta.get_latest_score_for_symbol(symbol, db_path=db_path)
    if latest is not None and latest.get("price") is not None:
        return latest["price"], False

    return position["buy_price"], False


def _record_snapshot(run_id: str, scores: list[dict], db_path) -> None:
    """Laskee ja tallentaa tarkistusajon salkun tilannekuvan (vaihe 4a).

    Salkun absoluuttinen tuotto ei kerro mallista mitaan nousumarkkinassa -
    ainoa luku joka vastaa kysymykseen "toimiiko tama" on ero SPY:hyn.
    Siksi tama lasketaan ja tallennetaan jo nyt, jotta tuottokayra syntyy
    itsestaan eika sita tarvitse koota takautuvasti myohemmin.
    """
    positions = tietokanta.get_open_positions(db_path=db_path)
    cash = tietokanta.get_cash(db_path=db_path)
    scores_by_symbol = {score["symbol"]: score for score in scores}

    positions_value = 0.0
    stale_count = 0
    for position in positions:
        price, is_fresh = _resolve_position_price(position, scores_by_symbol, db_path)
        positions_value += position["quantity"] * price
        if not is_fresh:
            stale_count += 1

    total_value = cash + positions_value

    benchmark_row = tietokanta.get_market_reference(
        run_id, BENCHMARK_SYMBOL, db_path=db_path
    )
    benchmark_price = benchmark_row["price"] if benchmark_row is not None else None

    # Lahtopiste haetaan ENNEN taman ajon tilannekuvan tallennusta, jotta
    # taman ajon oma rivi ei voi vahingossa toimia omana lahtopisteenaan.
    earlier_snapshots = tietokanta.get_snapshots(db_path=db_path)
    baseline = next(
        (s for s in earlier_snapshots if s["benchmark_price"] is not None), None
    )

    tietokanta.insert_snapshot(
        run_id=run_id,
        snapshot_date=date.today().isoformat(),
        cash=cash,
        positions_value=positions_value,
        total_value=total_value,
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_price=benchmark_price,
        db_path=db_path,
    )

    if stale_count > 0:
        _loki.warning(
            f"Ajo {run_id}: {stale_count} position arvo laskettu vanhalla tai "
            f"ostohinnalla (ei tuoretta pisteytysta talta ajolta)"
        )

    salkku_teksti = (
        f"salkku {_format_usd(total_value)} USD "
        f"(kateinen {_format_usd(cash)} + positiot {_format_usd(positions_value)})"
    )

    if baseline is None:
        _loki.info(f"Ajo {run_id}: {salkku_teksti} | vertailu alkaa tasta ajosta")
        return

    if benchmark_price is None:
        _loki.info(
            f"Ajo {run_id}: {salkku_teksti} | SPY-vertailu ei kaytettavissa "
            f"talle ajolle (vertailukurssi puuttuu)"
        )
        return

    portfolio_return = (total_value / baseline["total_value"] - 1) * 100
    benchmark_return = (benchmark_price / baseline["benchmark_price"] - 1) * 100
    diff = portfolio_return - benchmark_return

    _loki.info(
        f"Ajo {run_id}: {salkku_teksti} | tuotto {portfolio_return:+.2f} % | "
        f"{BENCHMARK_SYMBOL} {benchmark_return:+.2f} % | ero {diff:+.2f} %-yks"
    )


def run(run_id: str, run_type: str, db_path=tietokanta.DEFAULT_DB_PATH) -> str:
    """Lukee ajon ehdokkaat, pisteytykset ja päätökset ja lokittaa määrät.

    Tarkistusajossa laskee lisäksi salkun tilannekuvan (ks. _record_snapshot).
    """
    candidates = tietokanta.get_candidates(run_id, advanced_only=False, db_path=db_path)
    scores = tietokanta.get_scores(run_id, db_path=db_path)
    decisions = tietokanta.get_decisions(run_id, db_path=db_path)

    summary = (
        f"ajo {run_id} ({run_type}): {len(candidates)} ehdokasta, "
        f"{len(scores)} pisteytysta, {len(decisions)} paatosta"
    )
    _loki.info(summary)

    if run_type == "tarkistus":
        _record_snapshot(run_id, scores, db_path)

    return summary
