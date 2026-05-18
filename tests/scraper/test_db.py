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


def test_init_schema_creates_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r[0] for r in rows]
    assert "pages" in names
    assert "scraped_content" in names


def test_insert_pending_dedupes(conn):
    assert db.insert_pending(conn, "https://www.nhs.uk/baby/", None) is True
    assert db.insert_pending(conn, "https://www.nhs.uk/baby/", None) is False
    rows = conn.execute("SELECT COUNT(*) FROM pages").fetchone()
    assert rows[0] == 1


def test_check_constraint_rejects_invalid_status(conn):
    db.insert_pending(conn, "https://www.nhs.uk/baby/", None)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE pages SET status = 'bogus' WHERE url = 'https://www.nhs.uk/baby/'")


def test_check_constraint_rejects_invalid_classification(conn):
    db.insert_pending(conn, "https://www.nhs.uk/baby/", None)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE pages SET classification = 'video' WHERE url = 'https://www.nhs.uk/baby/'"
        )


def test_foreign_key_rejects_orphan_scraped_content(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scraped_content (url, time_scraped, html_content) VALUES (?, ?, ?)",
            ("https://www.nhs.uk/orphan/", "2026-05-18T12:00:00+00:00", "<html/>"),
        )
        conn.commit()


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


def test_mark_processed_updates_fields(conn):
    url = "https://www.nhs.uk/baby/"
    db.insert_pending(conn, url, None)
    db.mark_processed(conn, url, "article", attempt_count=1)
    row = conn.execute(
        "SELECT status, classification, attempt_count FROM pages WHERE url = ?",
        (url,),
    ).fetchone()
    assert row == ("processed", "article", 1)


def test_mark_failed_sets_reason(conn):
    url = "https://www.nhs.uk/baby/oops/"
    db.insert_pending(conn, url, None)
    db.mark_failed(conn, url, "http_404", attempt_count=1)
    row = conn.execute(
        "SELECT status, failure_reason, attempt_count FROM pages WHERE url = ?",
        (url,),
    ).fetchone()
    assert row == ("failed", "http_404", 1)


def test_mark_redirected_records_target(conn):
    src = "https://www.nhs.uk/baby/old/"
    dst = "https://www.nhs.uk/baby/new/"
    db.insert_pending(conn, src, None)
    db.mark_redirected(conn, src, dst, attempt_count=1)
    row = conn.execute(
        "SELECT status, redirect_target_url FROM pages WHERE url = ?", (src,)
    ).fetchone()
    assert row == ("redirected", dst)


def test_reset_failed_to_pending(conn):
    url = "https://www.nhs.uk/baby/x/"
    db.insert_pending(conn, url, None)
    db.mark_failed(conn, url, "http_500", attempt_count=3)
    assert db.reset_failed_to_pending(conn) == 1
    row = conn.execute(
        "SELECT status, attempt_count, failure_reason FROM pages WHERE url = ?",
        (url,),
    ).fetchone()
    assert row == ("pending", 0, None)


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
