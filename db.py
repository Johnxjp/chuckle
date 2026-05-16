"""SQLite connection, schema bootstrap, and read-only query helper."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

DEFAULT_DB_PATH = "chuckle.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT NOT NULL CHECK(type IN (
                         'Feed', 'Sleep', 'Diaper', 'Bath',
                         'Tummy time', 'Story time', 'Pump',
                         'Meds', 'Growth', 'Temp'
                     )),
    start_time       DATETIME NOT NULL,
    end_time         DATETIME,
    duration_minutes INTEGER,

    notes            TEXT,

    feed_mode            TEXT CHECK(feed_mode IN ('breast', 'bottle', 'solids')),
    feed_left_minutes    INTEGER,
    feed_right_minutes   INTEGER,
    feed_bottle_volume   REAL,
    feed_bottle_units    TEXT CHECK(feed_bottle_units IN ('ml', 'oz')),
    feed_bottle_type     TEXT CHECK(feed_bottle_type IN (
                             'Breast Milk', 'Formula', 'Tube Feeding',
                             'Cow Milk', 'Goat Milk', 'Soy Milk', 'Other'
                         )),
    feed_solids_food     TEXT,
    feed_solids_reaction TEXT CHECK(feed_solids_reaction IN ('LOVED', 'MEH', 'HATED', 'ALLERGIC')),

    diaper_kind        TEXT CHECK(diaper_kind IN ('pee', 'poo', 'both', 'dry')),
    diaper_colour      TEXT CHECK(diaper_colour IN (
                           'yellow', 'brown', 'black', 'green', 'red', 'gray'
                       )),
    diaper_consistency TEXT CHECK(diaper_consistency IN (
                           'solid', 'loose', 'runny', 'mucousy',
                           'hard', 'pebbles', 'diarrhea'
                       )),
    diaper_amount      TEXT CHECK(diaper_amount IN ('small', 'medium', 'large')),

    pump_volume_ml     INTEGER,

    growth_weight       REAL,
    growth_weight_units TEXT CHECK(growth_weight_units IN ('kg', 'lbs.oz')),

    meds_medicine   TEXT,
    meds_dose       REAL,
    meds_dose_units TEXT CHECK(meds_dose_units IN ('ml', 'oz', 'tsp', 'drops')),

    temp_value REAL,
    temp_units TEXT CHECK(temp_units IN ('C', 'F'))
);

CREATE INDEX IF NOT EXISTS idx_events_type  ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_time);
"""

EVENT_COLUMNS = (
    "type",
    "start_time",
    "end_time",
    "duration_minutes",
    "notes",
    "feed_mode",
    "feed_left_minutes",
    "feed_right_minutes",
    "feed_bottle_volume",
    "feed_bottle_units",
    "feed_bottle_type",
    "feed_solids_food",
    "feed_solids_reaction",
    "diaper_kind",
    "diaper_colour",
    "diaper_consistency",
    "diaper_amount",
    "pump_volume_ml",
    "growth_weight",
    "growth_weight_units",
    "meds_medicine",
    "meds_dose",
    "meds_dose_units",
    "temp_value",
    "temp_units",
)


def init_db(path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def replace_events(conn: sqlite3.Connection, events: Iterable[dict[str, Any]]) -> int:
    placeholders = ", ".join(f":{c}" for c in EVENT_COLUMNS)
    columns = ", ".join(EVENT_COLUMNS)
    insert_sql = f"INSERT INTO events ({columns}) VALUES ({placeholders})"

    rows = [{col: event.get(col) for col in EVENT_COLUMNS} for event in events]

    with conn:
        conn.execute("DELETE FROM events")
        conn.executemany(insert_sql, rows)
    return len(rows)


def run_select(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    stripped = sql.strip().lower()
    if not stripped.startswith("select"):
        raise ValueError("only SELECT statements are allowed")
    cursor = conn.execute(sql)
    return [dict(row) for row in cursor.fetchall()]
