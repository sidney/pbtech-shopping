"""
Session-scoped SQLite database for PB Tech product data.

One mixed-purpose `products` table with columns for cables and monitors.
Ugly but allows cross-category queries without UNIONs; storage waste
negligible at ~500 row scale.
"""

import json
import os
import sqlite3
from pathlib import Path

DB_DIR = Path.home() / ".cache" / "pbtech"
DB_PATH = DB_DIR / "session.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    part            TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    title           TEXT,
    subtitle        TEXT,
    url             TEXT,
    price           REAL,       -- NZD inc GST

    -- Cable fields
    gbps            REAL,
    max_watts       REAL,
    length_m        REAL,
    conn1           TEXT,
    conn2           TEXT,
    braided         INTEGER,    -- 0/1

    -- Monitor fields
    resolution_w    INTEGER,
    resolution_h    INTEGER,
    refresh_hz      INTEGER,
    panel_type      TEXT,
    screen_inches   REAL,

    -- Raw data
    raw_specs       TEXT,       -- JSON object of original spec rows
    llm_normalized  INTEGER DEFAULT 0,
    scraped_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cable
    ON products(category, gbps, max_watts, price);
CREATE INDEX IF NOT EXISTS idx_monitor
    ON products(category, resolution_w, refresh_hz, price);
"""


def get_connection() -> sqlite3.Connection:
    """Return a connection to the session database, creating it if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def reset_db():
    """Drop and recreate the session database."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    # WAL files
    for suffix in ("-wal", "-shm"):
        p = DB_PATH.with_suffix(DB_PATH.suffix + suffix)
        if p.exists():
            p.unlink()


def upsert_product(conn: sqlite3.Connection, row: dict):
    """Insert or replace a product row. `row` keys must match column names."""
    cols = [
        "part", "category", "title", "subtitle", "url", "price",
        "gbps", "max_watts", "length_m", "conn1", "conn2", "braided",
        "resolution_w", "resolution_h", "refresh_hz", "panel_type",
        "screen_inches", "raw_specs", "llm_normalized",
    ]
    values = [row.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    conn.execute(
        f"INSERT OR REPLACE INTO products ({col_names}) VALUES ({placeholders})",
        values,
    )


def run_query(conn: sqlite3.Connection, sql: str, limit: int = 20) -> dict:
    """
    Execute a read-only SQL query and return compact pipe-delimited output.
    Rejects anything that isn't a SELECT.
    Returns dict with 'columns', 'rows', 'row_count', 'truncated'.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped.upper().startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed."}

    # Enforce hard cap
    hard_cap = 100
    effective_limit = min(limit, hard_cap)

    # Wrap in a subquery to enforce limit without trusting user SQL
    wrapped = f"SELECT * FROM ({stripped}) LIMIT {effective_limit + 1}"

    cur = conn.execute(wrapped)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    truncated = len(rows) > effective_limit
    if truncated:
        rows = rows[:effective_limit]

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


def format_query_result(result: dict) -> str:
    """Format query result as compact pipe-delimited table."""
    if "error" in result:
        return f"ERROR: {result['error']}"

    if result["row_count"] == 0:
        return "No results."

    # Abbreviate common column names for token economy
    abbreviations = {
        "price": "$",
        "price_nzd_inc_gst": "$",
        "length_m": "m",
        "max_watts": "W",
        "screen_inches": "in",
        "resolution_w": "res_w",
        "resolution_h": "res_h",
        "refresh_hz": "hz",
        "panel_type": "panel",
        "llm_normalized": "llm",
    }
    headers = [abbreviations.get(c, c) for c in result["columns"]]

    lines = [" | ".join(str(h) for h in headers)]
    for row in result["rows"]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                # Drop trailing zeros
                cells.append(f"{v:g}")
            else:
                cells.append(str(v))
        lines.append(" | ".join(cells))

    output = "\n".join(lines)
    if result["truncated"]:
        output += f"\n... truncated (showing {result['row_count']} rows)"
    return output


def session_stats(conn: sqlite3.Connection) -> dict:
    """Return summary stats about the current session DB."""
    cur = conn.execute("SELECT COUNT(*) FROM products")
    total = cur.fetchone()[0]

    cur = conn.execute(
        "SELECT category, COUNT(*) FROM products GROUP BY category ORDER BY category"
    )
    by_category = {row[0]: row[1] for row in cur.fetchall()}

    return {"total_products": total, "by_category": by_category}
