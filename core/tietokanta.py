"""SQLite-skeema ja CRUD-funktiot - agenttien yhteinen rajapinta."""

import sqlite3
from datetime import datetime
from pathlib import Path

from core.asetukset import INITIAL_CAPITAL

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "sijoitusanalysaattori.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    initial_capital REAL NOT NULL,
    cash REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trend_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT,
    score REAL,
    reason_is_valid INTEGER,
    reason_note TEXT,
    reference_price REAL,
    rank INTEGER,
    advanced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS technical_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    risk_reward_score REAL NOT NULL,
    price REAL,
    indicators_json TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('osta', 'pida', 'myy', 'hylatty')),
    rationale TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('aktiivinen', 'suljettu')),
    buy_price REAL NOT NULL,
    quantity REAL NOT NULL,
    stake_usd REAL NOT NULL,
    stop_loss REAL,
    target_price REAL,
    buy_date TEXT NOT NULL,
    buy_run_id TEXT NOT NULL,
    sell_price REAL,
    sell_date TEXT,
    pnl_usd REAL,
    pnl_pct REAL,
    last_checked TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_open_symbol
ON positions(symbol) WHERE status = 'aktiivinen';

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('kesken', 'valmis', 'keskeytyi')),
    error TEXT,
    settings_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    git_commit TEXT
);
"""


def open_connection(db_path=DEFAULT_DB_PATH):
    """Avaa SQLite-yhteyden ja luo väliin jäävät hakemistot tarvittaessa."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_database(db_path=DEFAULT_DB_PATH):
    """Luo skeeman jos puuttuu ja alustaa portfolio-rivin INITIAL_CAPITALiin.

    Turvallinen ajaa useaan kertaan samaan kantaan.
    """
    connection = open_connection(db_path)
    try:
        connection.executescript(SCHEMA)

        row_count = connection.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
        if row_count == 0:
            connection.execute(
                "INSERT INTO portfolio "
                "(id, initial_capital, cash, currency, updated_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (
                    INITIAL_CAPITAL,
                    INITIAL_CAPITAL,
                    "USD",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def new_run_id() -> str:
    """Muodostaa aikaleimapohjaisen ajo-id:n."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_cash(db_path=DEFAULT_DB_PATH) -> float:
    """Palauttaa portfolion käteissaldon."""
    connection = open_connection(db_path)
    try:
        row = connection.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("portfolio-taulu on tyhjä - aja init_database() ensin")
        return row["cash"]
    finally:
        connection.close()


def adjust_cash(delta: float, db_path=DEFAULT_DB_PATH) -> float:
    """Muuttaa käteissaldoa delta:lla ja palauttaa uuden saldon.

    Nostaa ValueErrorin jos saldo menisi negatiiviseksi.
    """
    connection = open_connection(db_path)
    try:
        row = connection.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("portfolio-taulu on tyhjä - aja init_database() ensin")

        new_balance = row["cash"] + delta
        if new_balance < 0:
            raise ValueError(
                f"cash menisi negatiiviseksi: {row['cash']} + {delta} = {new_balance}"
            )

        connection.execute(
            "UPDATE portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (new_balance, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return new_balance
    finally:
        connection.close()


def get_open_positions(db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Palauttaa kaikki tilassa 'aktiivinen' olevat positiot."""
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM positions WHERE status = 'aktiivinen'"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def has_open_position(symbol: str, db_path=DEFAULT_DB_PATH) -> bool:
    """Kertoo onko symbolilla jo aktiivinen positio."""
    connection = open_connection(db_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM positions WHERE symbol = ? AND status = 'aktiivinen' "
            "LIMIT 1",
            (symbol,),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def insert_candidate(
    run_id: str,
    run_type: str,
    symbol: str,
    source: str,
    reason: str | None = None,
    score: float | None = None,
    reason_is_valid: bool | None = None,
    reason_note: str | None = None,
    reference_price: float | None = None,
    rank: int | None = None,
    advanced: int = 0,
    db_path=DEFAULT_DB_PATH,
) -> int:
    """Lisää rivin trend_candidates-tauluun ja palauttaa sen id:n."""
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO trend_candidates (
                run_id, run_type, symbol, source, reason, score, reason_is_valid,
                reason_note, reference_price, rank, advanced, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_type,
                symbol,
                source,
                reason,
                score,
                None if reason_is_valid is None else int(reason_is_valid),
                reason_note,
                reference_price,
                rank,
                int(advanced),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_candidates(
    run_id: str, advanced_only: bool = True, db_path=DEFAULT_DB_PATH
) -> list[dict]:
    """Palauttaa ajon ehdokkaat, oletuksena vain jatkoon menneet (advanced=1)."""
    connection = open_connection(db_path)
    try:
        query = "SELECT * FROM trend_candidates WHERE run_id = ?"
        params = [run_id]
        if advanced_only:
            query += " AND advanced = 1"
        query += " ORDER BY id"

        rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def insert_score(
    run_id: str,
    run_type: str,
    symbol: str,
    risk_reward_score: float,
    price: float | None = None,
    indicators_json: str | None = None,
    rationale: str | None = None,
    db_path=DEFAULT_DB_PATH,
) -> int:
    """Lisää rivin technical_scores-tauluun ja palauttaa sen id:n."""
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO technical_scores (
                run_id, run_type, symbol, risk_reward_score, price,
                indicators_json, rationale, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_type,
                symbol,
                risk_reward_score,
                price,
                indicators_json,
                rationale,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_scores(run_id: str, db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Palauttaa ajon kaikki tekniset pisteytykset."""
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM technical_scores WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def insert_decision(
    run_id: str,
    run_type: str,
    symbol: str,
    decision: str,
    rationale: str | None = None,
    db_path=DEFAULT_DB_PATH,
) -> int:
    """Lisää rivin decisions-tauluun ja palauttaa sen id:n."""
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO decisions (
                run_id, run_type, symbol, decision, rationale, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_type,
                symbol,
                decision,
                rationale,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def open_position(
    symbol: str,
    buy_price: float,
    stake_usd: float,
    buy_run_id: str,
    stop_loss: float | None = None,
    target_price: float | None = None,
    db_path=DEFAULT_DB_PATH,
) -> int:
    """Avaa uuden position ja vähentää sen hinnan käteisestä samassa transaktiossa.

    Nostaa ValueErrorin jos buy_price ei ole positiivinen tai käteinen menisi
    negatiiviseksi.
    """
    if buy_price <= 0:
        raise ValueError(f"buy_price on oltava positiivinen, saatiin {buy_price}")

    quantity = stake_usd / buy_price
    now = datetime.now().isoformat(timespec="seconds")

    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO positions (
                symbol, status, buy_price, quantity, stake_usd, stop_loss,
                target_price, buy_date, buy_run_id
            ) VALUES (?, 'aktiivinen', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                buy_price,
                quantity,
                stake_usd,
                stop_loss,
                target_price,
                now,
                buy_run_id,
            ),
        )
        position_id = cursor.lastrowid

        row = connection.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("portfolio-taulu on tyhjä - aja init_database() ensin")

        new_balance = row["cash"] - stake_usd
        if new_balance < 0:
            raise ValueError(
                f"cash menisi negatiiviseksi: {row['cash']} - {stake_usd} "
                f"= {new_balance}"
            )

        connection.execute(
            "UPDATE portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (new_balance, now),
        )

        connection.commit()
        return position_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def start_run(
    run_id: str,
    run_type: str,
    settings_json: str,
    model_version: str,
    git_commit: str | None = None,
    db_path=DEFAULT_DB_PATH,
) -> None:
    """Lisää runs-tauluun rivin tilassa 'kesken' ajon alkaessa."""
    connection = open_connection(db_path)
    try:
        connection.execute(
            """
            INSERT INTO runs (
                run_id, run_type, started_at, status, settings_json,
                model_version, git_commit
            ) VALUES (?, ?, ?, 'kesken', ?, ?, ?)
            """,
            (
                run_id,
                run_type,
                datetime.now().isoformat(timespec="seconds"),
                settings_json,
                model_version,
                git_commit,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def finish_run(
    run_id: str,
    status: str,
    error: str | None = None,
    db_path=DEFAULT_DB_PATH,
) -> None:
    """Päivittää runs-rivin lopputilaan ('valmis' tai 'keskeytyi')."""
    connection = open_connection(db_path)
    try:
        connection.execute(
            "UPDATE runs SET finished_at = ?, status = ?, error = ? WHERE run_id = ?",
            (
                datetime.now().isoformat(timespec="seconds"),
                status,
                error,
                run_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_run(run_id: str, db_path=DEFAULT_DB_PATH) -> dict | None:
    """Palauttaa yhden runs-rivin, tai None jos run_id:tä ei löydy."""
    connection = open_connection(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def get_decisions(run_id: str, db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Palauttaa ajon kaikki päätökset."""
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM decisions WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
