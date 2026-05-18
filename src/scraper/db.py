"""SQLite schema and helpers for the NHS baby scraper."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    discovered_from_url TEXT,
    time_first_seen TEXT NOT NULL,
    classification TEXT CHECK (classification IN ('article', 'index')),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'processed', 'failed', 'redirected', 'blocked_by_robots'
    )),
    redirect_target_url TEXT,
    failure_reason TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt TEXT
);

CREATE TABLE IF NOT EXISTS scraped_content (
    url TEXT PRIMARY KEY REFERENCES pages(url),
    time_scraped TEXT NOT NULL,
    html_content TEXT NOT NULL,
    markdown_content TEXT
);

CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    """Open a connection with foreign-key enforcement enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create both tables (idempotent)."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def insert_pending(conn: sqlite3.Connection, url: str, discovered_from: str | None) -> bool:
    """Insert a new `pending` row, ignoring duplicates.

    Returns True if a row was inserted, False if already present.
    """
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO pages (
            url, discovered_from_url, time_first_seen, status, attempt_count
        ) VALUES (?, ?, ?, 'pending', 0)
        """,
        (url, discovered_from, _now_iso()),
    )
    conn.commit()
    return cur.rowcount > 0


def next_pending(conn: sqlite3.Connection) -> str | None:
    """Return the oldest pending URL (FIFO) or None."""
    row = conn.execute(
        "SELECT url FROM pages WHERE status = 'pending' "
        "ORDER BY time_first_seen ASC, url ASC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def mark_processed(
    conn: sqlite3.Connection,
    url: str,
    classification: str,
    attempt_count: int,
) -> None:
    conn.execute(
        """
        UPDATE pages
        SET status = 'processed',
            classification = ?,
            failure_reason = NULL,
            redirect_target_url = NULL,
            attempt_count = ?,
            last_attempt = ?
        WHERE url = ?
        """,
        (classification, attempt_count, _now_iso(), url),
    )
    conn.commit()


def mark_failed(
    conn: sqlite3.Connection,
    url: str,
    failure_reason: str,
    attempt_count: int,
) -> None:
    conn.execute(
        """
        UPDATE pages
        SET status = 'failed',
            failure_reason = ?,
            attempt_count = ?,
            last_attempt = ?
        WHERE url = ?
        """,
        (failure_reason, attempt_count, _now_iso(), url),
    )
    conn.commit()


def mark_redirected(
    conn: sqlite3.Connection,
    url: str,
    target_url: str,
    attempt_count: int,
) -> None:
    conn.execute(
        """
        UPDATE pages
        SET status = 'redirected',
            redirect_target_url = ?,
            attempt_count = ?,
            last_attempt = ?
        WHERE url = ?
        """,
        (target_url, attempt_count, _now_iso(), url),
    )
    conn.commit()


def mark_blocked_by_robots(conn: sqlite3.Connection, url: str) -> None:
    conn.execute(
        """
        UPDATE pages
        SET status = 'blocked_by_robots',
            last_attempt = ?
        WHERE url = ?
        """,
        (_now_iso(), url),
    )
    conn.commit()


def upsert_content(conn: sqlite3.Connection, url: str, html: str) -> None:
    """Insert or update scraped_content for a URL."""
    conn.execute(
        """
        INSERT INTO scraped_content (url, time_scraped, html_content)
        VALUES (?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            time_scraped = excluded.time_scraped,
            html_content = excluded.html_content
        """,
        (url, _now_iso(), html),
    )
    conn.commit()


def reset_failed_to_pending(conn: sqlite3.Connection) -> int:
    """Reset all `failed` rows to `pending` and zero attempt_count.

    Returns the number of rows reset.
    """
    cur = conn.execute(
        """
        UPDATE pages
        SET status = 'pending',
            attempt_count = 0,
            failure_reason = NULL
        WHERE status = 'failed'
        """
    )
    conn.commit()
    return cur.rowcount


def reset_all_for_force(conn: sqlite3.Connection) -> int:
    """Reset every row except blocked_by_robots back to pending.

    Returns the number of rows reset.
    """
    cur = conn.execute(
        """
        UPDATE pages
        SET status = 'pending',
            attempt_count = 0,
            failure_reason = NULL,
            redirect_target_url = NULL
        WHERE status != 'blocked_by_robots'
        """
    )
    conn.commit()
    return cur.rowcount


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) FROM pages GROUP BY status").fetchall()
    return {status: count for status, count in rows}


def classification_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT classification, COUNT(*) FROM pages "
        "WHERE status = 'processed' GROUP BY classification"
    ).fetchall()
    return {classification or "unknown": count for classification, count in rows}


def failure_reason_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT failure_reason, COUNT(*) FROM pages WHERE status = 'failed' GROUP BY failure_reason"
    ).fetchall()
    return {reason or "unknown": count for reason, count in rows}


def page_exists(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM pages WHERE url = ?", (url,)).fetchone()
    return row is not None


def insert_processed_url(conn: sqlite3.Connection, url: str, discovered_from: str | None) -> None:
    """Insert a URL directly as processed (used for in-scope redirect targets)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO pages (
            url, discovered_from_url, time_first_seen, status, attempt_count
        ) VALUES (?, ?, ?, 'pending', 0)
        """,
        (url, discovered_from, _now_iso()),
    )
    conn.commit()


def seed_urls(
    conn: sqlite3.Connection, urls: Iterable[str], discovered_from: str | None = None
) -> int:
    """Seed multiple URLs as pending. Returns number of new rows inserted."""
    inserted = 0
    for url in urls:
        if insert_pending(conn, url, discovered_from):
            inserted += 1
    return inserted
