"""SQLite-skeema ja CRUD-funktiot - agenttien yhteinen rajapinta."""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from core.asetukset import INITIAL_CAPITAL

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "sijoitusanalysaattori.db"
# Kirjoita-kerran-arkisto (ks. CLAUDE.md). Sama polku jota
# agents/trendinmetsastaja.py kayttaa raakavastauksille - yksi totuuden
# lahde, jotta molemmat kirjoittavat samaan <run_id>-kansioon.
ARKISTO_DIR = DATA_DIR / "arkisto"

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
    stop_loss REAL,
    target_price REAL,
    rr_ratio REAL,
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
    sell_run_id TEXT,
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

CREATE TABLE IF NOT EXISTS market_reference (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    cash REAL NOT NULL,
    positions_value REAL NOT NULL,
    total_value REAL NOT NULL,
    benchmark_symbol TEXT,
    benchmark_price REAL,
    holdings_return_pct REAL,
    created_at TEXT NOT NULL
);
"""


def open_connection(db_path=DEFAULT_DB_PATH):
    """Avaa SQLite-yhteyden ja luo väliin jäävät hakemistot tarvittaessa."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _schema_columns() -> dict[str, list[sqlite3.Row]]:
    """Palauttaa skeeman taulut ja sarakkeet suoraan SQLiteltä luettuna.

    SCHEMA ajetaan muistinvaraiseen kantaan ja tulos luetaan PRAGMA:lla,
    jotta jäsentäminen ei ole kiinni merkkijonon muotoilusta (sisennys,
    rivinvaihdot, monirivinen CHECK-ehto) niin kuin regexillä olisi.
    PRAGMA table_info antaa jokaisesta sarakkeesta nimen, tyypin,
    notnull-lipun, oletusarvon ja pk-lipun - juuri sen mitä migraatio
    tarvitsee.
    """
    temp = sqlite3.connect(":memory:")
    temp.row_factory = sqlite3.Row
    try:
        temp.executescript(SCHEMA)
        tables = [
            row["name"]
            for row in temp.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: list(temp.execute(f"PRAGMA table_info({table})")) for table in tables
        }
    finally:
        temp.close()


def _migrate_missing_columns(connection) -> list[str]:
    """Lisää kantaan sarakkeet jotka ovat skeemassa mutta puuttuvat taulusta.

    Vertaa _schema_columns():n (SQLiten oma jäsennys) tulosta elävän kannan
    PRAGMA table_info:iin taulu kerrallaan ja lisää puuttuvat ALTER TABLE
    ... ADD COLUMN:lla. Palauttaa lisättyjen sarakkeiden nimet muodossa
    "taulu.sarake" - tyhjä lista jos kanta oli jo ajan tasalla. Ei koskaan
    poista mitään: elävässä kannassa olevaa ylimääräistä saraketta ei
    kosketa, eikä puuttuvaa taulua luoda tässä (sen hoitaa executescript).

    SQLite ei osaa lisätä NOT NULL- tai PRIMARY KEY -saraketta ilman
    oletusarvoa olemassa olevaan tauluun. Tällaista saraketta ei ohiteta
    hiljaa - se palauttaisi täsmälleen sen ongelman jota tämä funktio
    korjaa - vaan nostetaan RuntimeError joka kertoo mitä tehdä (ks.
    CLAUDE.md: uudet sarakkeet nullable tai oletusarvolla).
    """
    added: list[str] = []
    for table, schema_columns in _schema_columns().items():
        existing = {
            row["name"]: row
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column in schema_columns:
            name = column["name"]
            if name in existing:
                continue

            if column["pk"] or (column["notnull"] and column["dflt_value"] is None):
                kind = "PRIMARY KEY" if column["pk"] else "NOT NULL ilman oletusarvoa"
                raise RuntimeError(
                    f"sarake {table}.{name} on {kind} eika sita voi lisata "
                    f"ALTER TABLE:lla olemassa olevaan tauluun - lisaa "
                    f"DEFAULT tai kirjoita migraatio kasin"
                )

            ddl = f"ALTER TABLE {table} ADD COLUMN {name} {column['type'] or 'TEXT'}"
            if column["dflt_value"] is not None:
                ddl += f" DEFAULT {column['dflt_value']}"
            if column["notnull"]:
                ddl += " NOT NULL"
            connection.execute(ddl)
            added.append(f"{table}.{name}")

    return added


def init_database(db_path=DEFAULT_DB_PATH) -> list[str]:
    """Luo skeeman jos puuttuu, migratoi puuttuvat sarakkeet ja alustaa portfolio-rivin.

    Turvallinen ajaa useaan kertaan samaan kantaan. Palauttaa lisättyjen
    sarakkeiden listan (ks. _migrate_missing_columns) - tyhjä jos kanta oli
    jo ajan tasalla. core/tietokanta.py ei itse lokita mitään, joten
    kutsuja (core/orkestroija.py) lokittaa tämän jos lista ei ole tyhjä.
    """
    connection = open_connection(db_path)
    try:
        connection.executescript(SCHEMA)
        added_columns = _migrate_missing_columns(connection)

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
        return added_columns
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


def get_cooldown_symbols(
    cooldown_days: int, today: date, db_path=DEFAULT_DB_PATH
) -> dict[str, str]:
    """Palauttaa symbolit joiden uudelleenoston jaahy on yha voimassa.

    Avaimena symboli, arvona jaahyn paattymispaiva ISO-muodossa. Perustuu
    suljettujen positioiden symbolikohtaiseen viimeisimpaan sell_date:en -
    jos sama symboli on suljettu useaan kertaan, vain viimeisin myyntipaiva
    ratkaisee. today annetaan parametrina eika lueta kellosta, jotta funktio
    on testattavissa ilman ajan jaadyttamista.
    """
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            """
            SELECT symbol, MAX(sell_date) AS viimeisin_myynti
            FROM positions
            WHERE sell_date IS NOT NULL
            GROUP BY symbol
            """
        ).fetchall()
    finally:
        connection.close()

    cooldowns: dict[str, str] = {}
    for row in rows:
        sell_date = date.fromisoformat(row["viimeisin_myynti"][:10])
        cooldown_until = sell_date + timedelta(days=cooldown_days)
        if cooldown_until > today:
            cooldowns[row["symbol"]] = cooldown_until.isoformat()
    return cooldowns


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
    stop_loss: float | None = None,
    target_price: float | None = None,
    rr_ratio: float | None = None,
    db_path=DEFAULT_DB_PATH,
) -> int:
    """Lisää rivin technical_scores-tauluun ja palauttaa sen id:n."""
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO technical_scores (
                run_id, run_type, symbol, risk_reward_score, price,
                indicators_json, rationale, stop_loss, target_price, rr_ratio,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_type,
                symbol,
                risk_reward_score,
                price,
                indicators_json,
                rationale,
                stop_loss,
                target_price,
                rr_ratio,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_latest_score_for_symbol(symbol: str, db_path=DEFAULT_DB_PATH) -> dict | None:
    """Palauttaa symbolin viimeisimman technical_scores-rivin (mika tahansa ajo).

    Kaytetaan tilannekuvan positioarvostuksessa (Agentti 4) kun symbolilla ei
    ole tuoretta pisteytysta talta ajolta - haetaan silloin viimeisin aiempi
    hinta sen sijaan etta positio jaisi kokonaan arvostamatta.
    """
    connection = open_connection(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM technical_scores WHERE symbol = ? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return dict(row) if row is not None else None
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


def close_position(
    symbol: str, sell_price: float, sell_run_id: str, db_path=DEFAULT_DB_PATH
) -> dict:
    """Sulkee symbolin aktiivisen position ja kasvattaa käteistä myyntitulolla.

    Peilikuva open_position():lle, yksi yhteys ja yksi transaktio. Käteinen
    kasvaa myyntitulolla (quantity * sell_price), ei alkuperäisellä panoksella
    - voitto ja tappio syntyvät juuri tästä erotuksesta. Nostaa ValueErrorin
    jos sell_price ei ole positiivinen tai symbolilla ei ole aktiivista
    positiota. Palauttaa suljetun position rivin sanakirjana, jotta kutsuja
    voi lokittaa toteutuneen tuloksen.
    """
    if sell_price <= 0:
        raise ValueError(f"sell_price on oltava positiivinen, saatiin {sell_price}")

    now = datetime.now().isoformat(timespec="seconds")

    connection = open_connection(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM positions WHERE symbol = ? AND status = 'aktiivinen'",
            (symbol,),
        ).fetchone()
        if row is None:
            raise ValueError(f"{symbol}: ei aktiivista positiota suljettavaksi")

        proceeds = row["quantity"] * sell_price
        pnl_usd = proceeds - row["stake_usd"]
        pnl_pct = pnl_usd / row["stake_usd"] * 100

        connection.execute(
            """
            UPDATE positions
            SET status = 'suljettu', sell_price = ?, sell_date = ?,
                sell_run_id = ?, pnl_usd = ?, pnl_pct = ?, last_checked = ?
            WHERE id = ?
            """,
            (sell_price, now, sell_run_id, pnl_usd, pnl_pct, now, row["id"]),
        )

        cash_row = connection.execute(
            "SELECT cash FROM portfolio WHERE id = 1"
        ).fetchone()
        if cash_row is None:
            raise RuntimeError("portfolio-taulu on tyhjä - aja init_database() ensin")

        new_balance = cash_row["cash"] + proceeds
        connection.execute(
            "UPDATE portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (new_balance, now),
        )

        connection.commit()

        closed = dict(row)
        closed.update(
            status="suljettu",
            sell_price=sell_price,
            sell_date=now,
            sell_run_id=sell_run_id,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            last_checked=now,
        )
        return closed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_last_checked(symbol: str, db_path=DEFAULT_DB_PATH) -> None:
    """Päivittää aktiivisen position last_checked-aikaleiman nykyhetkeen.

    Käytetään kun positio pidetään - jokainen tarkistettu positio saa
    aikaleiman riippumatta päätöksestä.
    """
    connection = open_connection(db_path)
    try:
        connection.execute(
            "UPDATE positions SET last_checked = ? "
            "WHERE symbol = ? AND status = 'aktiivinen'",
            (datetime.now().isoformat(timespec="seconds"), symbol),
        )
        connection.commit()
    finally:
        connection.close()


def _write_run_metadata(run_id: str, db_path=DEFAULT_DB_PATH) -> None:
    """Kirjoittaa runs-rivin nykysisällön arkistoon (<run_id>/_ajo.json).

    Ilman tätä arkistokansio jäisi orvoksi jos sen kanta joskus poistetaan -
    ainoa jäljelle jäävä konteksti olisi silloin pelkkä kansion nimi. Rivi
    luetaan takaisin kannasta (get_run) sen sijaan että sama sisältö
    koottaisiin toiseen kertaan täällä - yksi totuuden lähde.
    """
    row = get_run(run_id, db_path=db_path)
    archive_dir = ARKISTO_DIR / run_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    file_path = archive_dir / "_ajo.json"
    with open(file_path, "w", encoding="utf-8") as file_handle:
        json.dump(row, file_handle, indent=2)


def start_run(
    run_id: str,
    run_type: str,
    settings_json: str,
    model_version: str,
    git_commit: str | None = None,
    db_path=DEFAULT_DB_PATH,
) -> None:
    """Lisää runs-tauluun rivin tilassa 'kesken' ajon alkaessa.

    Kirjoittaa myös arkistoon metatiedoston (ks. _write_run_metadata).
    """
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

    _write_run_metadata(run_id, db_path=db_path)


def finish_run(
    run_id: str,
    status: str,
    error: str | None = None,
    db_path=DEFAULT_DB_PATH,
) -> None:
    """Päivittää runs-rivin lopputilaan ('valmis' tai 'keskeytyi').

    Päivittää myös arkiston metatiedoston (ks. _write_run_metadata).
    """
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

    _write_run_metadata(run_id, db_path=db_path)


def mark_stale_runs_interrupted(db_path=DEFAULT_DB_PATH) -> int:
    """Merkitsee päättymättä jääneet ajot keskeytyneiksi. Palauttaa määrän.

    Ajo jää tilaan 'kesken' jos prosessi katkeaa kesken kaiken (kone menee
    nukkumaan, terminaali suljetaan, kaatuminen) - ilman tätä rivi jäisi
    'kesken'-tilaan ikuisiksi ajoiksi. Kutsuja vastaa siitä että tämä
    ajetaan ennen start_run():ia, ei sen jälkeen (ks. core/orkestroija.py).
    """
    now = datetime.now().isoformat(timespec="seconds")
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            UPDATE runs
            SET status = 'keskeytyi', finished_at = ?,
                error = 'ajo ei paattynyt (prosessi katkesi)'
            WHERE status = 'kesken'
            """,
            (now,),
        )
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


def get_recent_runs(limit: int = 20, db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Palauttaa viimeisimmat ajot uusin ensin.

    run_id on aikaleimapohjainen (ks. new_run_id()), joten se jarjestyy
    aikajarjestykseen myos merkkijonovertailulla - ei tarvita erillista
    aikaleimasaraketta jarjestykseen.
    """
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_best_scores(
    days: int = 30, limit: int = 10, db_path=DEFAULT_DB_PATH
) -> list[dict]:
    """Palauttaa parhaiten pisteytetyt symbolit annetulta ajanjaksolta.

    Yksi rivi per symboli: sen paras risk_reward_score viimeisen `days`
    vuorokauden ajalta ja se pisteytysrivi (mm. created_at) joka sen antoi.
    Jarjestys pisteiden mukaan laskevasti, enintaan `limit` riviä. Kaytetaan
    dashboard/nakyma.py:ssa - pisteytyksen paivamaara kulkee mukana, koska
    ilman sita vanha pistemaara nayttaisi valheellisesti "talla hetkella
    parhaalta".
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            """
            SELECT * FROM technical_scores
            WHERE created_at >= ?
            ORDER BY risk_reward_score DESC, created_at DESC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        connection.close()

    best_by_symbol: dict[str, dict] = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol not in best_by_symbol:
            best_by_symbol[symbol] = dict(row)

    return list(best_by_symbol.values())[:limit]


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


def insert_market_reference(
    run_id: str, symbol: str, price: float, db_path=DEFAULT_DB_PATH
) -> int:
    """Lisää rivin market_reference-tauluun ja palauttaa sen id:n."""
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO market_reference (run_id, symbol, price, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, symbol, price, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_market_reference(
    run_id: str, symbol: str, db_path=DEFAULT_DB_PATH
) -> dict | None:
    """Palauttaa yhden ajon vertailukurssirivin, tai None jos sitä ei ole."""
    connection = open_connection(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM market_reference WHERE run_id = ? AND symbol = ?",
            (run_id, symbol),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def insert_snapshot(
    run_id: str,
    snapshot_date: str,
    cash: float,
    positions_value: float,
    total_value: float,
    benchmark_symbol: str | None = None,
    benchmark_price: float | None = None,
    holdings_return_pct: float | None = None,
    db_path=DEFAULT_DB_PATH,
) -> int:
    """Lisää rivin portfolio_snapshots-tauluun ja palauttaa sen id:n.

    benchmark_price on nullable tarkoituksella: jos vertailuhaku epaonnistui,
    salkun arvo pitaa tallentaa silti - mittausta ei saa menettaa siksi ettei
    vertailukohta vastannut. holdings_return_pct on nullable samasta syystä
    kuin nolla avointa positiota: None, ei 0 - nolla nayttaisi tasaiselta
    tuotolta, mika olisi valhe (ks. agents/raportoija.py).
    """
    connection = open_connection(db_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO portfolio_snapshots (
                run_id, snapshot_date, cash, positions_value, total_value,
                benchmark_symbol, benchmark_price, holdings_return_pct, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                cash,
                positions_value,
                total_value,
                benchmark_symbol,
                benchmark_price,
                holdings_return_pct,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_snapshots(db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Palauttaa kaikki tilannekuvat aikajarjestyksessa (id:n mukaan)."""
    connection = open_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
