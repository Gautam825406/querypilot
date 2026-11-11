"""Deterministic demo seed: ~5,500 orders over 24 months (2024-2025), sample data only.

Seed version 2: the database stores its seed version in a `seed_meta` table,
and the backend reseeds automatically when the stored version differs, so a
larger dataset actually replaces stale small data on startup.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

SEED_VERSION = "2"

REGIONS = ["North", "South", "East", "West"]
CHANNELS = ["online", "retail", "partner"]
CATEGORIES = ["Electronics", "Apparel", "Home", "Beauty", "Sports"]
PLANS = ["Basic", "Pro", "Enterprise"]
_BASE_PRODUCTS = [
    ("Aurora Headphones", "Electronics", 89.0), ("Nimbus Laptop Stand", "Electronics", 49.0),
    ("Vertex Keyboard", "Electronics", 79.0), ("Lumen Lamp", "Home", 39.0),
    ("Atlas Sofa Cover", "Home", 59.0), ("Ember Candle Set", "Home", 24.0),
    ("Drift Running Shoes", "Sports", 119.0), ("Summit Yoga Mat", "Sports", 35.0),
    ("Trail Water Bottle", "Sports", 19.0), ("Cotton Tee", "Apparel", 29.0),
    ("Denim Jacket", "Apparel", 69.0), ("Wool Scarf", "Apparel", 25.0),
    ("Glow Serum", "Beauty", 45.0), ("Silk Shampoo", "Beauty", 22.0),
    ("Velvet Lipstick", "Beauty", 28.0), ("Pulse Smartwatch", "Electronics", 149.0),
    ("Echo Speaker", "Electronics", 99.0), ("Harbor Backpack", "Apparel", 59.0),
    ("Terra Plant Pot", "Home", 32.0), ("Aero Dumbbells", "Sports", 89.0),
]
# 100 products: the 20 base products in 5 variants each (same category,
# price scaled per variant). Deterministic — no RNG involved here.
_VARIANTS = [("", 1.0), (" Pro", 1.25), (" Mini", 0.8), (" Max", 1.5), (" Lite", 0.65)]
PRODUCTS = [
    (f"{name}{suffix}", cat, round(price * mult, 2))
    for (name, cat, price) in _BASE_PRODUCTS
    for (suffix, mult) in _VARIANTS
]
assert len(PRODUCTS) == 100


def _ym(index: int, base_year: int = 2024) -> tuple[int, int]:
    """Month index 0..23 -> (year, month), starting Jan 2024."""
    return base_year + index // 12, index % 12 + 1


def generate_rows() -> dict:
    """Deterministic demo rows (seed 42), shared by the DuckDB and Postgres
    seeders so both databases hold the exact same sample dataset."""
    rng = random.Random(42)
    customers = []
    for i in range(1, 2001):
        region = REGIONS[i % 4] if i % 7 else rng.choice(REGIONS)
        channel = CHANNELS[i % 3]
        created = date(2023, 1, 1) + timedelta(days=rng.randrange(0, 700))
        customers.append((i, f"Customer {i}", f"customer{i}@example.com", f"+91-90000{i:05d}", region, channel, created))

    products = [(i, name, cat, price) for i, (name, cat, price) in enumerate(PRODUCTS, start=1)]

    orders = []
    oid = 1
    # ~5,460 orders spread across 24 months (Jan 2024 - Dec 2025), growing
    # over time, North/online weighted. 170 + 5*mi per month -> 5,460 total.
    for mi in range(24):
        year, month = _ym(mi)
        n = 170 + 5 * mi
        for _ in range(n):
            cust = rng.randrange(1, 2001)
            prod = rng.randrange(1, 101)
            price = PRODUCTS[prod - 1][2]
            qty = rng.choice([1, 1, 1, 2, 2, 3])
            amount = round(price * qty * rng.uniform(0.9, 1.1), 2)
            region = rng.choices(REGIONS, weights=[35, 25, 20, 20])[0]
            channel = rng.choices(CHANNELS, weights=[50, 30, 20])[0]
            day = rng.randrange(1, 29)
            status = "completed" if rng.random() < 0.94 else "cancelled"
            orders.append((oid, cust, prod, date(year, month, day), amount, qty, region, channel, status))
            oid += 1

    subs = []
    for i in range(1, 601):
        cust = rng.randrange(1, 2001)
        plan = rng.choices(PLANS, weights=[50, 35, 15])[0]
        price = {"Basic": 19.0, "Pro": 49.0, "Enterprise": 149.0}[plan]
        start_mi = rng.randrange(0, 24)
        start_year, start_month = _ym(start_mi)
        started = date(start_year, start_month, rng.randrange(1, 28))
        r = rng.random()
        if r < 0.62:
            status, ended = "active", None
        elif r < 0.88:
            status = "churned"
            end_mi = min(23, start_mi + rng.randrange(1, 5))
            end_year, end_month = _ym(end_mi)
            ended = date(end_year, end_month, rng.randrange(1, 28))
        else:
            status, ended = "paused", None
        subs.append((i, cust, plan, status, started, ended, price))
    return {"customers": customers, "products": products, "orders": orders, "subscriptions": subs}


def seed(db_path: Path) -> dict:
    rows = generate_rows()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR, email VARCHAR, phone VARCHAR, region VARCHAR, channel VARCHAR, created_at DATE)")
    con.execute("CREATE TABLE products (id INTEGER, name VARCHAR, category VARCHAR, price DECIMAL(10,2))")
    con.execute("CREATE TABLE orders (id INTEGER, customer_id INTEGER, product_id INTEGER, order_date DATE, amount DECIMAL(10,2), quantity INTEGER, region VARCHAR, channel VARCHAR, status VARCHAR)")
    con.execute("CREATE TABLE subscriptions (id INTEGER, customer_id INTEGER, plan VARCHAR, status VARCHAR, started_at DATE, ended_at DATE, monthly_price DECIMAL(10,2))")
    con.execute("CREATE TABLE seed_meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO seed_meta VALUES ('version', ?)", [SEED_VERSION])
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", rows["customers"])
    con.executemany("INSERT INTO products VALUES (?,?,?,?)", rows["products"])
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", rows["orders"])
    con.executemany("INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)", rows["subscriptions"])
    con.execute("CHECKPOINT")  # fold the WAL into the file; don't leave a multi-MB WAL behind
    con.close()
    return {k: len(v) for k, v in rows.items()}


def seed_version_duckdb(db_path: Path) -> str | None:
    """Stored seed version of an existing DuckDB file, or None if it was
    seeded before versions existed (or the file is missing/broken)."""
    if not db_path.exists():
        return None
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            row = con.execute("SELECT value FROM seed_meta WHERE key = 'version'").fetchone()
        except Exception:
            return None
        finally:
            con.close()
        return row[0] if row else None
    except Exception:
        return None


_PG_DDL = {
    "customers": "CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name VARCHAR, email VARCHAR, phone VARCHAR, region VARCHAR, channel VARCHAR, created_at DATE)",
    "products": "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, name VARCHAR, category VARCHAR, price NUMERIC(10,2))",
    "orders": "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER, order_date DATE, amount NUMERIC(10,2), quantity INTEGER, region VARCHAR, channel VARCHAR, status VARCHAR)",
    "subscriptions": "CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY, customer_id INTEGER, plan VARCHAR, status VARCHAR, started_at DATE, ended_at DATE, monthly_price NUMERIC(10,2))",
}

_PG_COLS = {
    "customers": "(id, name, email, phone, region, channel, created_at)",
    "products": "(id, name, category, price)",
    "orders": "(id, customer_id, product_id, order_date, amount, quantity, region, channel, status)",
    "subscriptions": "(id, customer_id, plan, status, started_at, ended_at, monthly_price)",
}


def seed_postgres(database_url: str) -> dict:
    """Seed the same deterministic demo dataset into Postgres (FR-15).

    This is a seed script, not the query path: it runs DDL/DML against the
    URL in DATABASE_URL. The query executor never writes. Safe to re-run:
    tables are created if missing and refilled from scratch.
    """
    from sqlalchemy import create_engine, text

    rows = generate_rows()
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as conn:
        for table, ddl in _PG_DDL.items():
            conn.execute(text(ddl))
        conn.execute(text("CREATE TABLE IF NOT EXISTS seed_meta (key VARCHAR, value VARCHAR)"))
        for table in ("orders", "subscriptions", "customers", "products"):
            conn.execute(text(f"TRUNCATE TABLE {table}"))
        conn.execute(text("TRUNCATE TABLE seed_meta"))
        conn.execute(text("INSERT INTO seed_meta VALUES ('version', :v)"), {"v": SEED_VERSION})
        for table, data in rows.items():
            cols = _PG_COLS[table]
            n = len(data[0])
            placeholders = ", ".join(f":v{i}" for i in range(n))
            stmt = text(f"INSERT INTO {table} {cols} VALUES ({placeholders})")
            conn.execute(stmt, [{f"v{i}": v for i, v in enumerate(row)} for row in data])
    return {k: len(v) for k, v in rows.items()}


def seed_version_postgres(database_url: str) -> str | None:
    """Stored seed version in Postgres, or None if unseeded / unreachable."""
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM seed_meta WHERE key = 'version'")).fetchone()
        return row[0] if row else None
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--postgres":
        import os
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise SystemExit("DATABASE_URL is not set.")
        print(seed_postgres(url))
    else:
        p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "demo.duckdb"
        print(seed(p))
