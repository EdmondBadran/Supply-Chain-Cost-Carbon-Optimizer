import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "instance" / "supplychain.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    node_type TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    storage_cost_annual REAL DEFAULT 0,
    energy_kwh_annual REAL DEFAULT 0,
    grid_intensity REAL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    order_ref TEXT,
    order_date TEXT,
    customer_id TEXT,
    origin_id INTEGER NOT NULL REFERENCES nodes(id),
    dest_id INTEGER NOT NULL REFERENCES nodes(id),
    units INTEGER DEFAULT 1,
    weight_kg REAL NOT NULL,
    mode TEXT NOT NULL,
    product_category TEXT,
    order_value REAL DEFAULT 0,
    returned INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    origin_id INTEGER NOT NULL REFERENCES nodes(id),
    dest_id INTEGER NOT NULL REFERENCES nodes(id),
    mode TEXT NOT NULL,
    order_count INTEGER NOT NULL,
    total_weight_kg REAL NOT NULL,
    total_value REAL NOT NULL,
    return_count INTEGER NOT NULL,
    distance_km REAL NOT NULL,
    transport_cost REAL,
    handling_cost REAL,
    returns_cost REAL,
    transport_co2e REAL,
    packaging_co2e REAL,
    warehouse_co2e REAL,
    returns_co2e REAL,
    UNIQUE (origin_id, dest_id, mode)
);

CREATE TABLE IF NOT EXISTS effort_tags (
    edge_id INTEGER PRIMARY KEY REFERENCES edges(id) ON DELETE CASCADE,
    effort TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_orders_origin ON orders(origin_id);
CREATE INDEX IF NOT EXISTS idx_orders_dest ON orders(dest_id);
CREATE INDEX IF NOT EXISTS idx_edges_origin ON edges(origin_id);
"""


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def reset(conn):
    """Drop loaded data but keep the schema, so a new upload starts clean."""
    for table in ("effort_tags", "edges", "orders", "nodes"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def summary(conn):
    """What is currently loaded, or None if the database is empty."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS orders,
               SUM(weight_kg) AS weight_kg,
               MIN(order_date) AS first_order,
               MAX(order_date) AS last_order
        FROM orders
        """
    ).fetchone()
    if not row or not row["orders"]:
        return None

    nodes = conn.execute(
        "SELECT node_type, COUNT(*) AS count FROM nodes GROUP BY node_type"
    ).fetchall()
    counts = {n["node_type"]: n["count"] for n in nodes}

    return {
        "orders": row["orders"],
        "weight_kg": row["weight_kg"] or 0,
        "first_order": row["first_order"],
        "last_order": row["last_order"],
        "warehouses": counts.get("warehouse", 0),
        "customers": counts.get("customer", 0),
        "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
        "countries": conn.execute(
            "SELECT COUNT(DISTINCT country) FROM nodes WHERE country IS NOT NULL"
        ).fetchone()[0],
    }
