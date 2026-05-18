"""Robots.txt loader using urllib.robotparser (strict, case-sensitive)."""

from __future__ import annotations

import logging
from urllib.robotparser import RobotFileParser

import httpx

from src.scraper.constants import ROBOTS_URL, USER_AGENT

log = logging.getLogger(__name__)


def load_robots(*, client: httpx.Client | None = None) -> RobotFileParser:
    """Fetch robots.txt once. Returns a parser that permits everything on failure."""
    parser = RobotFileParser()
    parser.set_url(ROBOTS_URL)

    own_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True
        )

    try:
        response = client.get(ROBOTS_URL)
        if response.status_code >= 400:
            log.warning(
                "robots.txt fetch returned HTTP %s — defaulting to allow-all",
                response.status_code,
            )
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())
    except httpx.HTTPError as exc:
        log.warning("robots.txt fetch failed (%s) — defaulting to allow-all", exc)
        parser.parse([])
    finally:
        if own_client:
            client.close()

    return parser


def is_allowed(parser: RobotFileParser, url: str) -> bool:
    """Wrap RobotFileParser.can_fetch with our user-agent string."""
    return parser.can_fetch(USER_AGENT, url)
