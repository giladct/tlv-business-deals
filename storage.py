import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "flights.db"


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flights (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                destination   TEXT NOT NULL,
                country       TEXT,
                iata_code     TEXT,
                price_usd     REAL,
                cabin         TEXT DEFAULT 'Business',
                origin        TEXT DEFAULT 'TLV',
                scraped_at    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                destination   TEXT NOT NULL,
                price_usd     REAL,
                scraped_at    TEXT NOT NULL
            )
        """)
        conn.commit()


def save_flights(flights: list[dict]) -> int:
    if not flights:
        return 0
    now = datetime.utcnow().isoformat()
    rows = []
    for f in flights:
        rows.append((
            f.get("destination", ""),
            f.get("country", ""),
            f.get("iata_code", ""),
            f.get("price_usd"),
            f.get("cabin", "Business"),
            f.get("origin", "TLV"),
            now,
        ))
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("""
            INSERT INTO flights (destination, country, iata_code, price_usd, cabin, origin, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        # also track history for trend analysis later
        conn.executemany("""
            INSERT INTO price_history (destination, price_usd, scraped_at)
            VALUES (?, ?, ?)
        """, [(r[0], r[4], now) for r in rows])
        conn.commit()
    return len(rows)


def load_latest_flights() -> list[dict]:
    """Return the most recent scrape per destination."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT f.*
            FROM flights f
            INNER JOIN (
                SELECT destination, MAX(scraped_at) AS latest
                FROM flights
                GROUP BY destination
            ) latest ON f.destination = latest.destination AND f.scraped_at = latest.latest
            ORDER BY price_usd ASC
        """).fetchall()
    return [dict(r) for r in rows]


def load_all_flights() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM flights ORDER BY scraped_at DESC").fetchall()
    return [dict(r) for r in rows]
