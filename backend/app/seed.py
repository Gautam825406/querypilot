"""Deterministic demo seed: ~600 orders over 12 months (2025), sample data only."""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

REGIONS = ["North", "South", "East", "West"]
CHANNELS = ["online", "retail", "partner"]
CATEGORIES = ["Electronics", "Apparel", "Home", "Beauty", "Sports"]
PLANS = ["Basic", "Pro", "Enterprise"]
PRODUCTS = [
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


def seed(db_path: Path) -> dict:
    rng = random.Random(42)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR, email VARCHAR, phone VARCHAR, region VARCHAR, channel VARCHAR, created_at DATE)")
    con.execute("CREATE TABLE products (id INTEGER, name VARCHAR, category VARCHAR, price DECIMAL(10,2))")
    con.execute("CREATE TABLE orders (id INTEGER, customer_id INTEGER, product_id INTEGER, order_date DATE, amount DECIMAL(10,2), quantity INTEGER, region VARCHAR, channel VARCHAR, status VARCHAR)")
    con.execute("CREATE TABLE subscriptions (id INTEGER, customer_id INTEGER, plan VARCHAR, status VARCHAR, started_at DATE, ended_at DATE, monthly_price DECIMAL(10,2))")

    customers = []
    for i in range(1, 201):
        region = REGIONS[i % 4] if i % 7 else rng.choice(REGIONS)
        channel = CHANNELS[i % 3]
        created = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 700))
        customers.append((i, f"Customer {i}", f"customer{i}@example.com", f"+91-90000{i:05d}", region, channel, created))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)

    for i, (name, cat, price) in enumerate(PRODUCTS, start=1):
        con.execute("INSERT INTO products VALUES (?,?,?,?)", [i, name, cat, price])

    orders = []
    oid = 1
    # ~600 orders spread across 2025, heavier in later months, North/online weighted.
    for month in range(1, 13):
        n = 38 + month * 2  # 40..62 per month -> 612 total
        for _ in range(n):
            cust = rng.randrange(1, 201)
            prod = rng.randrange(1, 21)
            price = PRODUCTS[prod - 1][2]
            qty = rng.choice([1, 1, 1, 2, 2, 3])
            amount = round(price * qty * rng.uniform(0.9, 1.1), 2)
            region = rng.choices(REGIONS, weights=[35, 25, 20, 20])[0]
            channel = rng.choices(CHANNELS, weights=[50, 30, 20])[0]
            day = rng.randrange(1, 29)
            status = "completed" if rng.random() < 0.94 else "cancelled"
            orders.append((oid, cust, prod, date(2025, month, day), amount, qty, region, channel, status))
            oid += 1
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", orders)

    subs = []
    for i in range(1, 121):
        cust = rng.randrange(1, 201)
        plan = rng.choices(PLANS, weights=[50, 35, 15])[0]
        price = {"Basic": 19.0, "Pro": 49.0, "Enterprise": 149.0}[plan]
        start_month = rng.randrange(1, 13)
        started = date(2025, start_month, rng.randrange(1, 28))
        r = rng.random()
        if r < 0.62:
            status, ended = "active", None
        elif r < 0.88:
            status = "churned"
            end_month = min(12, start_month + rng.randrange(1, 5))
            ended = date(2025, end_month, rng.randrange(1, 28))
        else:
            status, ended = "paused", None
        subs.append((i, cust, plan, status, started, ended, price))
    con.executemany("INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)", subs)
    con.close()
    return {"customers": len(customers), "products": len(PRODUCTS), "orders": len(orders), "subscriptions": len(subs)}


if __name__ == "__main__":
    import sys
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "demo.duckdb"
    print(seed(p))
