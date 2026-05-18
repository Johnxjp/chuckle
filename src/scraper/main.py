"""CLI entry point for the NHS baby scraper."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import timedelta

from src.scraper import crawler, db, fetcher, robots
from src.scraper.constants import (
    DEFAULT_DB_PATH,
    EXCEPTION_URLS,
    MAX_PAGES,
    SEED_URL,
)


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return
    info_handler = logging.StreamHandler(sys.stdout)
    info_handler.setLevel(level)
    info_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    info_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    err_handler = logging.StreamHandler(sys.stderr)
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    root.addHandler(info_handler)
    root.addHandler(err_handler)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="src.scraper.main", description="NHS baby scraper")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to the scraper SQLite database (default: %(default)s)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES,
        help="Safety cap on total processed pages",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset failed rows to pending before crawling",
    )
    group.add_argument(
        "--force",
        action="store_true",
        help="Reset every row (except blocked_by_robots) to pending and re-fetch",
    )
    return parser.parse_args(argv)


def _print_summary(stats: crawler.CrawlStats, elapsed: float, conn) -> None:
    duration = timedelta(seconds=int(elapsed))
    status = db.status_counts(conn)
    classifications = db.classification_counts(conn)
    failures = db.failure_reason_counts(conn)
    print(f"Crawl complete in {duration}")
    print(
        "  Processed: {n} (articles: {a}, indexes: {i})".format(
            n=status.get("processed", 0),
            a=classifications.get("article", 0),
            i=classifications.get("index", 0),
        )
    )
    if failures:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(failures.items()))
        print(f"  Failed:    {status.get('failed', 0)} ({breakdown})")
    else:
        print(f"  Failed:    {status.get('failed', 0)}")
    print(f"  Redirected: {status.get('redirected', 0)}")
    print(f"  Blocked by robots: {status.get('blocked_by_robots', 0)}")
    print(f"  Pending (unfinished): {status.get('pending', 0)}")


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging()
    log = logging.getLogger(__name__)

    conn = db.connect(args.db)
    db.init_schema(conn)

    if args.force:
        reset = db.reset_all_for_force(conn)
        log.info("--force: reset %d rows to pending", reset)
    elif args.retry_failed:
        reset = db.reset_failed_to_pending(conn)
        log.info("--retry-failed: reset %d failed rows to pending", reset)

    db.insert_pending(conn, SEED_URL, discovered_from=None)
    for url in EXCEPTION_URLS:
        db.insert_pending(conn, url, discovered_from=None)

    client = fetcher.build_client()
    try:
        robots_parser = robots.load_robots(client=client)
        start = time.monotonic()
        stats = crawler.run_crawl(
            conn,
            client=client,
            robots_parser=robots_parser,
            max_pages=args.max_pages,
        )
        elapsed = time.monotonic() - start
    finally:
        client.close()

    _print_summary(stats, elapsed, conn)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
