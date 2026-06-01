"""Tests for schema bootstrap, versioned migration, and event round-trip."""

from __future__ import annotations

import sqlite3

import db

# The events-table definition that shipped before SCHEMA_VERSION 2: identical to
# the current schema but without 'Solids' in the type CHECK constraint. Used to
# simulate an on-disk database created by an older build.
_OLD_SCHEMA_SQL = """
CREATE TABLE events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT NOT NULL CHECK(type IN (
                         'Feed', 'Sleep', 'Diaper', 'Bath',
                         'Tummy time', 'Story time', 'Pump',
                         'Meds', 'Growth', 'Temp'
                     )),
    start_time       DATETIME NOT NULL,
    feed_solids_food     TEXT,
    feed_solids_reaction TEXT
);
"""


def _solids_event() -> dict:
    return {
        "type": "Solids",
        "start_time": "2026-05-31 16:00:00",
        "feed_solids_food": "Pumpkin",
        "feed_solids_reaction": "LOVED",
    }


def test_fresh_db_accepts_solids(tmp_path):
    conn = db.init_db(str(tmp_path / "fresh.db"))
    assert db.replace_events(conn, [_solids_event()]) == 1
    rows = db.run_select(conn, "SELECT type, feed_solids_food FROM events")
    assert rows == [{"type": "Solids", "feed_solids_food": "Pumpkin"}]


def test_stale_schema_is_rebuilt(tmp_path):
    # Reproduces the reported bug: a DB created by an older build (no 'Solids'
    # in the CHECK constraint, user_version 0) must be migrated by init_db so
    # that inserting a Solids event no longer trips the constraint.
    path = tmp_path / "stale.db"
    seed = sqlite3.connect(path)
    seed.executescript(_OLD_SCHEMA_SQL)
    seed.commit()
    seed.close()

    conn = db.init_db(str(path))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert db.replace_events(conn, [_solids_event()]) == 1


def test_current_version_db_preserves_data(tmp_path):
    # A DB already at the current schema version must NOT be wiped on re-open,
    # so an earlier upload survives an app restart (rehydration on page load).
    path = str(tmp_path / "current.db")
    conn = db.init_db(path)
    db.replace_events(conn, [_solids_event()])
    conn.close()

    reopened = db.init_db(path)
    total, _ = db.get_event_summary(reopened)
    assert total == 1
