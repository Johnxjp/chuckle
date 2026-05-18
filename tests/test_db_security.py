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
        # Direct mutations
        "INSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01 10:00:00')",
        "UPDATE events SET type='Feed' WHERE id=1",
        "DELETE FROM events",
        "DROP TABLE events",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA table_info(events)",
        # Multi-statement
        "SELECT * FROM events; DROP TABLE events",
        "SELECT 1; SELECT 2",
        "SELECT * FROM events; INSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01')",
        # Leading line comment hiding a mutation
        "-- just a comment\nDELETE FROM events",
        "-- another\n-- two\nINSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01')",
        # Leading block comment hiding a mutation
        "/* comment */ INSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01')",
        "/* multi\nline */ UPDATE events SET type='Feed'",
        # Multi-statement with comment in between
        "SELECT * FROM events; /* comment */ DROP TABLE events",
    ],
)
def test_run_select_rejects(conn, sql: str) -> None:
    with pytest.raises(ValueError, match="only SELECT statements are allowed"):
        db.run_select(conn, sql)


def test_run_select_allows_plain_select(conn) -> None:
    result = db.run_select(conn, "SELECT 1 AS n")
    assert result == [{"n": 1}]


def test_run_select_allows_select_with_inline_comment(conn) -> None:
    result = db.run_select(conn, "SELECT /* inline */ 1 AS n")
    assert result == [{"n": 1}]


def test_run_select_allows_select_with_trailing_semicolon_only(conn) -> None:
    result = db.run_select(conn, "SELECT 1 AS n;")
    assert result == [{"n": 1}]


def test_run_select_allows_select_with_leading_whitespace(conn) -> None:
    result = db.run_select(conn, "   SELECT 1 AS n")
    assert result == [{"n": 1}]


def test_get_schema_context_contains_ddl(conn) -> None:
    context = db.get_schema_context(conn)
    assert "CREATE TABLE" in context
    assert "events" in context


def test_get_schema_context_contains_column_descriptions(conn) -> None:
    context = db.get_schema_context(conn)
    assert "feed_left_minutes" in context
    assert "diaper_kind" in context


def test_get_schema_context_no_data_placeholder(conn) -> None:
    context = db.get_schema_context(conn)
    assert "(no data yet)" in context
