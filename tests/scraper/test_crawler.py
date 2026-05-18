"""Unit tests for scraper.crawler (link extraction + BFS loop)."""

from __future__ import annotations

from pathlib import Path
from urllib.robotparser import RobotFileParser

import httpx
import pytest

from src.scraper import crawler, db, fetcher
from src.scraper.constants import USER_AGENT
from src.scraper.crawler import extract_links

FIXTURES = Path(__file__).parent / "fixtures"


def _allow_all_robots() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse([])
    return parser


@pytest.fixture(autouse=True)
def _no_sleeps(monkeypatch):
    monkeypatch.setattr(crawler, "_polite_sleep", lambda: None)
    monkeypatch.setattr(fetcher, "_sleep_backoff", lambda _i: None)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "scraper.db"
    c = db.connect(str(path))
    db.init_schema(c)
    yield c
    c.close()


def _client():
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False, timeout=5.0)


def test_extract_links_returns_nine_baby_subtopics():
    html = (FIXTURES / "baby_index.html").read_text(encoding="utf-8")
    links = extract_links(html, "https://www.nhs.uk/baby/")
    expected = {
        "https://www.nhs.uk/baby/caring-for-a-newborn/",
        "https://www.nhs.uk/baby/support-and-services/",
        "https://www.nhs.uk/baby/breastfeeding-and-bottle-feeding/",
        "https://www.nhs.uk/baby/newborn-screening/",
        "https://www.nhs.uk/baby/newborn-twins-and-multiples/",
        "https://www.nhs.uk/baby/health/",
        "https://www.nhs.uk/baby/first-aid-and-safety/",
        "https://www.nhs.uk/baby/babys-development/",
        "https://www.nhs.uk/baby/weaning-and-feeding/",
    }
    assert set(links) == expected
    assert len(links) == 9


def test_extract_links_dedupes_and_normalises():
    html = """
    <html><body><main id='maincontent'>
      <a href="https://www.nhs.uk/baby/x/">a</a>
      <a href="https://www.nhs.uk/baby/x/#frag">b</a>
      <a href="https://www.nhs.uk/baby/x/?ref=1">c</a>
      <a href="mailto:foo@bar.com">d</a>
    </main></body></html>
    """
    links = extract_links(html, "https://www.nhs.uk/baby/")
    assert links == ["https://www.nhs.uk/baby/x/"]


def test_bfs_loop_orders_breadth_first_and_respects_cap(conn, httpx_mock):
    seed = "https://www.nhs.uk/baby/"
    httpx_mock.add_response(
        url=seed,
        status_code=200,
        headers={"Content-Type": "text/html"},
        text="""
        <html><body><main id='maincontent'>
          <a href="https://www.nhs.uk/baby/a/">a</a>
          <a href="https://www.nhs.uk/baby/b/">b</a>
          <p>Hello world this is a long enough paragraph for article scoring.</p>
        </main></body></html>
        """,
    )
    leaf_html = (
        "<html><body><main id='maincontent'>"
        "<p>Just a stub leaf with enough text.</p>"
        "</main></body></html>"
    )
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/a/",
        status_code=200,
        headers={"Content-Type": "text/html"},
        text=leaf_html,
    )
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/b/",
        status_code=200,
        headers={"Content-Type": "text/html"},
        text=leaf_html,
    )

    db.insert_pending(conn, seed, None)

    with _client() as client:
        stats = crawler.run_crawl(
            conn,
            client=client,
            robots_parser=_allow_all_robots(),
            max_pages=10,
        )

    assert stats.processed == 3
    rows = conn.execute("SELECT url, status FROM pages ORDER BY time_first_seen, url").fetchall()
    urls = [r[0] for r in rows]
    statuses = [r[1] for r in rows]
    assert urls[0] == seed
    assert set(urls) == {
        seed,
        "https://www.nhs.uk/baby/a/",
        "https://www.nhs.uk/baby/b/",
    }
    assert set(statuses) == {"processed"}


def test_max_pages_cap_stops_processing(conn, httpx_mock):
    seed = "https://www.nhs.uk/baby/"
    httpx_mock.add_response(
        url=seed,
        status_code=200,
        headers={"Content-Type": "text/html"},
        text="""
        <html><body><main id='maincontent'>
          <a href="https://www.nhs.uk/baby/never-fetched/">x</a>
        </main></body></html>
        """,
    )
    db.insert_pending(conn, seed, None)

    with _client() as client:
        stats = crawler.run_crawl(
            conn,
            client=client,
            robots_parser=_allow_all_robots(),
            max_pages=1,
        )

    assert stats.processed == 1
    pending = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'pending'").fetchone()[0]
    # the never-fetched URL was enqueued during seed processing but is now pending
    assert pending >= 1


def test_exception_url_skips_link_extraction(conn, httpx_mock):
    exception = "https://www.nhs.uk/pregnancy/labour-and-birth/early-days/"
    httpx_mock.add_response(
        url=exception,
        status_code=200,
        headers={"Content-Type": "text/html"},
        text="""
        <html><body><main id='maincontent'>
          <p>Genuine prose long enough to qualify as substantive content here.</p>
          <a href="https://www.nhs.uk/pregnancy/labour-and-birth/something-else/">link</a>
        </main></body></html>
        """,
    )
    db.insert_pending(conn, exception, None)

    with _client() as client:
        crawler.run_crawl(
            conn,
            client=client,
            robots_parser=_allow_all_robots(),
            max_pages=10,
        )

    urls = [r[0] for r in conn.execute("SELECT url FROM pages ORDER BY url").fetchall()]
    assert urls == [exception]


def test_robots_blocked_url_marked_and_not_fetched(conn, httpx_mock):
    blocked_url = "https://www.nhs.uk/baby/secret/"
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /baby/secret/"])

    db.insert_pending(conn, blocked_url, None)

    with _client() as client:
        crawler.run_crawl(
            conn,
            client=client,
            robots_parser=parser,
            max_pages=10,
        )

    status = conn.execute("SELECT status FROM pages WHERE url = ?", (blocked_url,)).fetchone()[0]
    assert status == "blocked_by_robots"
    # pytest-httpx asserts no unexpected requests at teardown


def test_in_scope_redirect_creates_target_row(conn, httpx_mock):
    src = "https://www.nhs.uk/baby/old/"
    dst = "https://www.nhs.uk/baby/new/"
    httpx_mock.add_response(
        url=src,
        status_code=301,
        headers={"Location": dst},
    )
    httpx_mock.add_response(
        url=dst,
        status_code=200,
        headers={"Content-Type": "text/html"},
        text="<html><body><main id='maincontent'><p>"
        + ("word " * 30)
        + "</p></main></body></html>",
    )
    db.insert_pending(conn, src, None)

    with _client() as client:
        crawler.run_crawl(
            conn,
            client=client,
            robots_parser=_allow_all_robots(),
            max_pages=10,
        )

    src_row = conn.execute(
        "SELECT status, redirect_target_url FROM pages WHERE url = ?", (src,)
    ).fetchone()
    dst_row = conn.execute(
        "SELECT status, classification FROM pages WHERE url = ?", (dst,)
    ).fetchone()
    assert src_row == ("redirected", dst)
    assert dst_row[0] == "processed"
    assert dst_row[1] == "article"
    content = conn.execute(
        "SELECT html_content FROM scraped_content WHERE url = ?", (dst,)
    ).fetchone()
    assert content is not None


def test_out_of_scope_redirect_drops_body(conn, httpx_mock):
    src = "https://www.nhs.uk/baby/leaves/"
    dst = "https://www.gov.uk/childcare/"
    httpx_mock.add_response(url=src, status_code=302, headers={"Location": dst})
    db.insert_pending(conn, src, None)

    with _client() as client:
        crawler.run_crawl(
            conn,
            client=client,
            robots_parser=_allow_all_robots(),
            max_pages=10,
        )

    src_status = conn.execute(
        "SELECT status, redirect_target_url FROM pages WHERE url = ?", (src,)
    ).fetchone()
    assert src_status == ("redirected", dst)
    dst_row = conn.execute("SELECT 1 FROM pages WHERE url = ?", (dst,)).fetchone()
    assert dst_row is None
    content = conn.execute("SELECT 1 FROM scraped_content WHERE url = ?", (dst,)).fetchone()
    assert content is None


def test_failed_url_records_attempt_count(conn, httpx_mock):
    url = "https://www.nhs.uk/baby/missing/"
    httpx_mock.add_response(url=url, status_code=404)
    db.insert_pending(conn, url, None)

    with _client() as client:
        crawler.run_crawl(
            conn,
            client=client,
            robots_parser=_allow_all_robots(),
            max_pages=10,
        )

    row = conn.execute(
        "SELECT status, failure_reason, attempt_count FROM pages WHERE url = ?", (url,)
    ).fetchone()
    assert row == ("failed", "http_404", 1)
