"""Security tests for db.run_select — SELECT-only guard (spec §5.2, T-3.2)."""

from __future__ import annotations

import pytest

import db


@pytest.fixture()
def conn():
    connection = db.init_db(":memory:")
    yield connection
    connection.close()


@pytest.mark.parametrize(
    "sql",
    [
        # Direct mutation — branch: doesn't start with SELECT after comment strip.
        "INSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01 10:00:00')",
        # Multi-statement — branch: trailing semicolon followed by non-whitespace.
        "SELECT * FROM events; DROP TABLE events",
        # Line comment hiding a mutation — branch: -- strip then re-check leading keyword.
        "-- just a comment\nDELETE FROM events",
        # Block comment hiding a mutation — branch: /* */ strip with DOTALL.
        "/* comment */ INSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01')",
    ],
)
def test_run_select_rejects(conn, sql: str) -> None:
    with pytest.raises(ValueError, match="only SELECT statements are allowed"):
        db.run_select(conn, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 AS n",
        "SELECT /* inline */ 1 AS n",
        "SELECT 1 AS n;",
        "   SELECT 1 AS n",
    ],
)
def test_run_select_allows_select_variants(conn, sql: str) -> None:
    assert db.run_select(conn, sql) == [{"n": 1}]


def test_get_schema_context_renders_ddl_columns_and_placeholder(conn) -> None:
    context = db.get_schema_context(conn)
    assert "CREATE TABLE" in context
    assert "events" in context
    assert "feed_left_minutes" in context
    assert "diaper_kind" in context
    assert "(no data yet)" in context
