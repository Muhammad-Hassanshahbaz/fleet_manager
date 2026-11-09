"""Database layer: SQLite connection + schema.

Raw data lives in three tables: vehicles, refuels, expenses.
Calculated fields (distance, km/L, cost/km ...) are NOT stored - they are
derived on demand in calculations.py so they can never go out of sync
with the raw records.
"""
import os
import sqlite3
from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "fleet.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reg_no            TEXT NOT NULL UNIQUE,
    make_model        TEXT NOT NULL,
    vehicle_type      TEXT NOT NULL,
    fuel_type         TEXT NOT NULL,
    tank_capacity     REAL,              -- litres, used for validation
    next_service_km   REAL,              -- maintenance reminder (bonus)
    next_service_date TEXT,              -- maintenance reminder (bonus)
    notes             TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS refuels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id      INTEGER NOT NULL REFERENCES vehicles(id),
    filled_at       TEXT NOT NULL,       -- 'YYYY-MM-DD HH:MM'
    odometer        REAL NOT NULL,       -- km
    litres          REAL NOT NULL,
    price_per_litre REAL NOT NULL,
    calc_total      REAL NOT NULL,       -- litres x price (system calculated)
    receipt_total   REAL NOT NULL,       -- amount actually paid (user entered/verified)
    full_tank       INTEGER NOT NULL DEFAULT 1,
    driver          TEXT,
    station         TEXT,
    notes           TEXT,
    override_note   TEXT,                -- filled when a user confirms a suspicious record
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id),
    date        TEXT NOT NULL,           -- 'YYYY-MM-DD'
    category    TEXT NOT NULL,
    description TEXT,
    amount      REAL NOT NULL,
    odometer    REAL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_refuels_vehicle ON refuels(vehicle_id, filled_at);
CREATE INDEX IF NOT EXISTS ix_expenses_vehicle ON expenses(vehicle_id, date);
"""


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()
