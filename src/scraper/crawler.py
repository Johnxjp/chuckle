"""BFS crawl loop and link extraction for the NHS baby scraper."""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from src.scraper import db, fetcher, robots
from src.scraper.classifier import classify
from src.scraper.constants import (
    MAX_PAGES,
    MAX_REDIRECTS,
    POLITENESS_DELAY_MAX_SECONDS,
    POLITENESS_DELAY_MIN_SECONDS,
    PROGRESS_INTERVAL,
)
from src.scraper.normaliser import normalise
from src.scraper.scope import is_exception_url, is_in_scope

log = logging.getLogger(__name__)


@dataclass
class CrawlStats:
    processed: int = 0
    failed: int = 0
    redirected: int = 0
    blocked: int = 0
    classifications: dict[str, int] = field(default_factory=dict)


def extract_links(html: str, base_url: str) -> list[str]:
    """Parse <a href> from <main> (fallback <body>), normalise, deduplicate."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main", id="maincontent") or soup.find("main") or soup.body
    if root is None:
        return []

    seen: list[str] = []
    seen_set: set[str] = set()
    for anchor in root.find_all("a", href=True):
        normalised = normalise(anchor["href"], base_url)
        if normalised is None or normalised in seen_set:
            continue
        seen.append(normalised)
        seen_set.add(normalised)
    return seen


def _polite_sleep() -> None:
    time.sleep(random.uniform(POLITENESS_DELAY_MIN_SECONDS, POLITENESS_DELAY_MAX_SECONDS))


def _follow_redirects(
    url: str,
    *,
    client: httpx.Client,
    robots_parser: RobotFileParser,
) -> fetcher.FetchResult:
    """Walk a redirect chain up to MAX_REDIRECTS hops.

    Returns the final non-redirect FetchResult, or a synthetic failed result
    with `redirect_loop` if the chain is too long, or the last hop's result
    if the chain ends on an out-of-scope target (so the caller can decide).
    """
    current = url
    hops = 0
    total_attempts = 0
    while True:
        result = fetcher.fetch(current, client=client)
        total_attempts += result.attempts
        if result.kind != "redirect":
            return fetcher.FetchResult(
                kind=result.kind,
                url=current,
                attempts=total_attempts,
                status=result.status,
                body=result.body,
                bytes_read=result.bytes_read,
                redirect_target=result.redirect_target,
                failure_reason=result.failure_reason,
                duration_ms=result.duration_ms,
            )

        hops += 1
        target_raw = result.redirect_target
        if not target_raw:
            return fetcher.FetchResult(
                kind="failed",
                url=current,
                attempts=total_attempts,
                status=result.status,
                failure_reason="redirect_loop",
                duration_ms=result.duration_ms,
            )

        target = normalise(target_raw, current)
        if target is None:
            return fetcher.FetchResult(
                kind="failed",
                url=current,
                attempts=total_attempts,
                status=result.status,
                failure_reason="redirect_loop",
                duration_ms=result.duration_ms,
            )

        if hops > MAX_REDIRECTS:
            return fetcher.FetchResult(
                kind="failed",
                url=url,
                attempts=total_attempts,
                status=result.status,
                failure_reason="redirect_loop",
                redirect_target=target,
                duration_ms=result.duration_ms,
            )

        if not robots.is_allowed(robots_parser, target):
            log.warning("redirect target blocked by robots.txt: %s", target)
            return fetcher.FetchResult(
                kind="redirect",
                url=url,
                attempts=total_attempts,
                status=result.status,
                redirect_target=target,
                duration_ms=result.duration_ms,
            )

        if not is_in_scope(target):
            return fetcher.FetchResult(
                kind="redirect",
                url=url,
                attempts=total_attempts,
                status=result.status,
                redirect_target=target,
                duration_ms=result.duration_ms,
            )

        if target == current:
            return fetcher.FetchResult(
                kind="failed",
                url=current,
                attempts=total_attempts,
                status=result.status,
                failure_reason="redirect_loop",
                duration_ms=result.duration_ms,
            )

        current = target
        _polite_sleep()


def _process_url(
    conn: sqlite3.Connection,
    url: str,
    *,
    client: httpx.Client,
    robots_parser: RobotFileParser,
    stats: CrawlStats,
) -> None:
    """Handle a single pending URL end-to-end and update stats + DB."""

    if not robots.is_allowed(robots_parser, url):
        log.warning("blocked by robots.txt: %s", url)
        db.mark_blocked_by_robots(conn, url)
        stats.blocked += 1
        return

    _polite_sleep()
    result = _follow_redirects(url, client=client, robots_parser=robots_parser)

    log.info(
        "%s status=%s duration=%dms bytes=%d",
        result.kind.upper(),
        result.status,
        result.duration_ms,
        result.bytes_read,
    )

    if result.kind == "failed":
        db.mark_failed(
            conn,
            url,
            failure_reason=result.failure_reason or "network_error",
            attempt_count=result.attempts,
        )
        stats.failed += 1
        return

    if result.kind == "redirect":
        target = result.redirect_target
        if target is None:
            db.mark_failed(conn, url, "redirect_loop", result.attempts)
            stats.failed += 1
            return
        db.mark_redirected(conn, url, target, result.attempts)
        stats.redirected += 1
        if is_in_scope(target):
            db.insert_pending(conn, target, discovered_from=url)
        return

    final_url = result.url
    classification = classify(result.body or "")
    log.info(
        "[%s] %s classification=%s duration=%dms bytes=%d",
        result.status,
        final_url,
        classification,
        result.duration_ms,
        result.bytes_read,
    )

    if final_url != url:
        if is_in_scope(final_url):
            db.insert_pending(conn, final_url, discovered_from=url)
            db.mark_redirected(conn, url, final_url, result.attempts)
            stats.redirected += 1
            db.mark_processed(conn, final_url, classification, result.attempts)
            if classification == "article":
                db.upsert_content(conn, final_url, result.body or "")
            stats.processed += 1
            stats.classifications[classification] = stats.classifications.get(classification, 0) + 1
        else:
            db.mark_redirected(conn, url, final_url, result.attempts)
            stats.redirected += 1
        return

    db.mark_processed(conn, url, classification, result.attempts)
    stats.processed += 1
    stats.classifications[classification] = stats.classifications.get(classification, 0) + 1

    if classification == "article":
        db.upsert_content(conn, url, result.body or "")

    if is_exception_url(url):
        return

    links = extract_links(result.body or "", base_url=final_url)
    for link in links:
        if is_in_scope(link):
            db.insert_pending(conn, link, discovered_from=url)


def run_crawl(
    conn: sqlite3.Connection,
    *,
    client: httpx.Client,
    robots_parser: RobotFileParser,
    max_pages: int = MAX_PAGES,
) -> CrawlStats:
    """Drive the BFS loop. Stop after `max_pages` processed pages."""
    stats = CrawlStats()
    while stats.processed + stats.failed + stats.redirected + stats.blocked < max_pages:
        url = db.next_pending(conn)
        if url is None:
            break
        _process_url(
            conn,
            url,
            client=client,
            robots_parser=robots_parser,
            stats=stats,
        )
        total_done = stats.processed + stats.failed + stats.redirected + stats.blocked
        if total_done % PROGRESS_INTERVAL == 0:
            pending = conn.execute(
                "SELECT COUNT(*) FROM pages WHERE status = 'pending'"
            ).fetchone()[0]
            log.info(
                "progress: %d processed, %d failed, %d pending, %d redirected",
                stats.processed,
                stats.failed,
                pending,
                stats.redirected,
            )
    return stats
