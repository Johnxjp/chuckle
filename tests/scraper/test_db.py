"""Unit tests for scraper.db."""

from __future__ import annotations

import sqlite3

import pytest

from src.scraper import db


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "scraper.db"
    c = db.connect(str(path))
    db.init_schema(c)
    yield c
    c.close()


def test_insert_pending_dedupes(conn):
    assert db.insert_pending(conn, "https://www.nhs.uk/baby/", None) is True
    assert db.insert_pending(conn, "https://www.nhs.uk/baby/", None) is False
    rows = conn.execute("SELECT COUNT(*) FROM pages").fetchone()
    assert rows[0] == 1


def test_check_constraint_smoke(conn):
    """One CHECK-constraint case to confirm the schema applied; SQLite enforces the rest."""
    db.insert_pending(conn, "https://www.nhs.uk/baby/", None)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE pages SET status = 'bogus' WHERE url = 'https://www.nhs.uk/baby/'")


def test_upsert_refreshes_content(conn):
    db.insert_pending(conn, "https://www.nhs.uk/baby/", None)
    db.upsert_content(conn, "https://www.nhs.uk/baby/", "<p>v1</p>")
    db.upsert_content(conn, "https://www.nhs.uk/baby/", "<p>v2</p>")
    row = conn.execute(
        "SELECT html_content FROM scraped_content WHERE url = ?",
        ("https://www.nhs.uk/baby/",),
    ).fetchone()
    assert row[0] == "<p>v2</p>"
    count = conn.execute("SELECT COUNT(*) FROM scraped_content").fetchone()[0]
    assert count == 1


@pytest.mark.parametrize(
    ("mark_fn", "args", "expected_cols", "expected_row"),
    [
        (
            db.mark_processed,
            ("article", 1),
            "status, classification, attempt_count",
            ("processed", "article", 1),
        ),
        (
            db.mark_failed,
            ("http_404", 1),
            "status, failure_reason, attempt_count",
            ("failed", "http_404", 1),
        ),
        (
            db.mark_redirected,
            ("https://www.nhs.uk/baby/new/", 1),
            "status, redirect_target_url",
            ("redirected", "https://www.nhs.uk/baby/new/"),
        ),
    ],
)
def test_mark_helpers_update_fields(conn, mark_fn, args, expected_cols, expected_row):
    url = "https://www.nhs.uk/baby/x/"
    db.insert_pending(conn, url, None)
    mark_fn(conn, url, *args)
    row = conn.execute(f"SELECT {expected_cols} FROM pages WHERE url = ?", (url,)).fetchone()
    assert tuple(row) == expected_row


def test_reset_all_for_force_skips_blocked(conn):
    a = "https://www.nhs.uk/baby/a/"
    b = "https://www.nhs.uk/baby/b/"
    db.insert_pending(conn, a, None)
    db.insert_pending(conn, b, None)
    db.mark_processed(conn, a, "article", 1)
    db.mark_blocked_by_robots(conn, b)
    assert db.reset_all_for_force(conn) == 1
    row_a = conn.execute("SELECT status FROM pages WHERE url = ?", (a,)).fetchone()
    row_b = conn.execute("SELECT status FROM pages WHERE url = ?", (b,)).fetchone()
    assert row_a[0] == "pending"
    assert row_b[0] == "blocked_by_robots"


def test_next_pending_returns_oldest_first(conn):
    db.insert_pending(conn, "https://www.nhs.uk/baby/a/", None)
    db.insert_pending(conn, "https://www.nhs.uk/baby/b/", None)
    assert db.next_pending(conn) == "https://www.nhs.uk/baby/a/"
