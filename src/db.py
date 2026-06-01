"""SQLite connection, schema bootstrap, and read-only query helper."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from typing import Any

_SQL_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
_MULTI_STMT_RE = re.compile(r";\s*\S")

DEFAULT_DB_PATH = "chuckle.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT NOT NULL CHECK(type IN (
                         'Feed', 'Solids', 'Sleep', 'Diaper', 'Bath',
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
    # check_same_thread=False: the connection is created in the Streamlit main
    # thread but used from the agent daemon thread. SQLite is safe here because
    # writes (CSV ingest) and reads (agent queries) never overlap in practice.
    conn = sqlite3.connect(path, check_same_thread=False)
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
    without_comments = _SQL_COMMENT_RE.sub(" ", sql)
    if not without_comments.strip().lower().startswith("select"):
        raise ValueError("only SELECT statements are allowed")
    if _MULTI_STMT_RE.search(without_comments):
        raise ValueError("only SELECT statements are allowed")
    cursor = conn.execute(sql)
    return [dict(row) for row in cursor.fetchall()]


def get_event_summary(conn: sqlite3.Connection) -> tuple[int, dict[str, int]]:
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if total == 0:
        return 0, {}
    rows = conn.execute("SELECT type, COUNT(*) FROM events GROUP BY type").fetchall()
    return total, {row[0]: row[1] for row in rows}


def get_schema_context(conn: sqlite3.Connection) -> str:
    from prompts import COLUMN_DESCRIPTIONS  # local import avoids circular dependency

    cursor = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name IN ('events', 'idx_events_type', 'idx_events_start')"
    )
    ddl = "\n\n".join(row[0] for row in cursor.fetchall())

    col_desc_lines = "\n".join(f"  {col}: {desc}" for col, desc in COLUMN_DESCRIPTIONS.items())

    event_types = [
        "Feed",
        "Solids",
        "Sleep",
        "Diaper",
        "Bath",
        "Tummy time",
        "Story time",
        "Pump",
        "Meds",
        "Growth",
        "Temp",
    ]
    samples_parts = []
    for event_type in event_types:
        rows = conn.execute("SELECT * FROM events WHERE type = ? LIMIT 3", (event_type,)).fetchall()
        if rows:
            rows_json = "\n".join(json.dumps(dict(row), default=str) for row in rows)
            samples_parts.append(f"{event_type}:\n{rows_json}")

    samples_block = "\n\n".join(samples_parts) if samples_parts else "(no data yet)"

    return (
        f"Database schema (live DDL):\n{ddl}\n\n"
        f"Column descriptions:\n{col_desc_lines}\n\n"
        f"Sample rows (up to 3 per event type):\n{samples_block}"
    )
